"""Bounded work, single-flight requests, in-memory user limits, and safe retries."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
import random
import time
from typing import Any, TypeVar

from aiogram.exceptions import TelegramRetryAfter

from bot.models.exceptions import DownloadTimeoutError, NetworkError, QueueFullError, RateLimitedError

T = TypeVar("T")
logger = logging.getLogger(__name__)


async def retry_async(operation: Callable[[], Awaitable[T]], *, attempts: int = 3,
                      sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                      jitter: Callable[[], float] = lambda: random.uniform(0.75, 1.25),
                      retry_network: bool = True) -> T:
    """Retry downloads and explicit RetryAfter; never retry uncertain Telegram sends."""
    for attempt in range(attempts):
        try:
            return await operation()
        except (NetworkError, TelegramRetryAfter) as exc:
            if isinstance(exc, NetworkError) and not retry_network:
                raise
            if attempt + 1 == attempts:
                raise
            delay = min(30.0, 2.0 ** attempt) * jitter()
            if isinstance(exc, TelegramRetryAfter):
                delay = max(delay, float(exc.retry_after))
            logger.info("Retry attempt=%d delay_seconds=%.2f error=%s", attempt + 2, delay, type(exc).__name__)
            await sleep(delay)
    raise ValueError("attempts must be positive")


@dataclass
class _Job:
    key: str
    operation: Callable[[], Awaitable[Any]]
    future: asyncio.Future
    waiters: int = 0
    task: asyncio.Task | None = None


class WorkQueue:
    """At most concurrency running plus capacity waiting, including shared jobs."""

    def __init__(self, *, concurrency: int = 3, capacity: int = 32, timeout: float = 300) -> None:
        if min(concurrency, capacity, timeout) <= 0:
            raise ValueError("Queue parameters must be positive")
        self.concurrency, self.capacity, self.timeout = concurrency, capacity, timeout
        self._queue: asyncio.Queue[_Job] = asyncio.Queue(capacity + concurrency)
        self._slots = asyncio.Semaphore(concurrency)
        self._jobs: dict[str, _Job] = {}
        self._inflight = 0
        self._workers: list[asyncio.Task] = []
        self._closing = False

    async def run(self, key: str, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closing:
            raise QueueFullError("Service is shutting down.")
        job = self._jobs.get(key)
        if job is None:
            if self._inflight >= self.capacity + self.concurrency:
                self._retire_cancelled_queued()
            if self._inflight >= self.capacity + self.concurrency:
                raise QueueFullError("Work queue is full.")
            future = asyncio.get_running_loop().create_future()
            future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
            job = _Job(key, operation, future)
            self._jobs[key] = job
            self._queue.put_nowait(job)
            self._inflight += 1
            if not self._workers:
                self._workers = [asyncio.create_task(self._worker(), name=f"download-worker-{i}")
                                 for i in range(self.concurrency)]
        job.waiters += 1
        try:
            async with asyncio.timeout(self.timeout):
                return await asyncio.shield(job.future)
        except TimeoutError as exc:
            raise DownloadTimeoutError("Queue and operation deadline exceeded.") from exc
        finally:
            job.waiters -= 1
            if not job.waiters and not job.future.done():
                job.future.cancel()
                if self._jobs.get(key) is job:
                    del self._jobs[key]
                if job.task is not None:
                    if not job.task.cancelling():
                        job.task.cancel()
                    await asyncio.gather(job.task, return_exceptions=True)

    def _retire_cancelled_queued(self) -> None:
        retained = []
        while not self._queue.empty():
            job = self._queue.get_nowait()
            self._queue.task_done()
            if job.future.cancelled():
                self._inflight -= 1
                if self._jobs.get(job.key) is job:
                    del self._jobs[job.key]
            else:
                retained.append(job)
        for job in retained:
            self._queue.put_nowait(job)

    async def _worker(self) -> None:
        while True:
            job = await self._queue.get()
            started = time.monotonic()
            try:
                if job.future.cancelled():
                    continue
                async with self._slots:
                    job.task = asyncio.create_task(job.operation())
                    try:
                        value = await asyncio.wait_for(job.task, self.timeout)
                    except TimeoutError:
                        if not job.future.done():
                            job.future.set_exception(DownloadTimeoutError("Operation deadline exceeded."))
                    except asyncio.CancelledError:
                        job.future.cancel()
                        if self._closing:
                            raise
                    except Exception as exc:
                        logger.warning("Operation failed error=%s", type(exc).__name__)
                        if not job.future.done():
                            job.future.set_exception(exc)
                    else:
                        if not job.future.done():
                            job.future.set_result(value)
            finally:
                if self._jobs.get(job.key) is job:
                    del self._jobs[job.key]
                self._inflight -= 1
                self._queue.task_done()
                logger.info("Operation finished duration_seconds=%.3f", time.monotonic() - started)

    async def close(self) -> None:
        self._closing = True
        for job in list(self._jobs.values()):
            job.future.cancel()
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._jobs.clear()
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
        self._inflight = 0


@dataclass
class _User:
    requests: deque[float] = field(default_factory=deque)
    active: int = 0
    last_notice: float = float("-inf")


class UserLimiter:
    """No persistent user data; bounded counters expire after the rate window."""

    def __init__(self, *, requests: int = 5, window: float = 60, cooldown: float = 2,
                 active_limit: int = 1, clock: Callable[[], float] = time.monotonic,
                 capacity: int = 10000) -> None:
        self.requests, self.window, self.cooldown = requests, window, cooldown
        self.active_limit, self.clock, self.capacity = active_limit, clock, capacity
        self._users: dict[int, _User] = {}
        self._last_prune = float("-inf")

    def acquire(self, user_id: int) -> None:
        now = self.clock()
        if now - self._last_prune >= self.window:
            self._users = {key: user for key, user in self._users.items()
                           if user.active or (user.requests and user.requests[-1] > now - self.window)}
            self._last_prune = now
        if user_id not in self._users and len(self._users) >= self.capacity:
            raise QueueFullError("Rate limiter capacity reached.")
        user = self._users.setdefault(user_id, _User())
        while user.requests and user.requests[0] <= now - self.window:
            user.requests.popleft()
        delay = 0.0
        if user.active >= self.active_limit:
            delay = max(delay, 1, self.cooldown)
        if user.requests:
            delay = max(delay, self.cooldown - (now - user.requests[-1]))
            if len(user.requests) >= self.requests:
                delay = max(delay, user.requests[0] + self.window - now)
        if delay > 0:
            notify = now - user.last_notice >= max(1, self.cooldown)
            if notify:
                user.last_notice = now
            raise RateLimitedError(delay, notify=notify)
        user.requests.append(now)
        user.active += 1

    def release(self, user_id: int) -> None:
        if user_id in self._users:
            self._users[user_id].active = max(0, self._users[user_id].active - 1)
