import asyncio

import pytest

from bot.models.exceptions import DownloadTimeoutError, QueueFullError, RateLimitedError, NetworkError, InvalidURLError
from bot.services.operations import WorkQueue, UserLimiter, retry_async


@pytest.mark.asyncio
async def test_three_users_run_and_extra_requests_queue() -> None:
    queue = WorkQueue(concurrency=3, capacity=4, timeout=2)
    gate, ready = asyncio.Event(), asyncio.Event()
    active = peak = entered = 0

    async def work():
        nonlocal active, peak, entered
        active += 1
        entered += 1
        peak = max(peak, active)
        if entered == 3:
            ready.set()
        try:
            await gate.wait()
            return "ok"
        finally:
            active -= 1

    tasks = [asyncio.create_task(queue.run(str(i), work)) for i in range(6)]
    try:
        await asyncio.wait_for(ready.wait(), 1)
        assert entered == 3
        gate.set()
        assert await asyncio.gather(*tasks) == ["ok"] * 6
        assert peak == 3
    finally:
        await queue.close()


@pytest.mark.asyncio
async def test_duplicate_jobs_share_one_result_and_waiter_cancel_is_isolated() -> None:
    queue = WorkQueue(concurrency=1, capacity=2, timeout=2)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def work():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return 42

    first = asyncio.create_task(queue.run("same", work))
    await started.wait()
    second = asyncio.create_task(queue.run("same", work))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == 42
    assert calls == 1
    await queue.close()


@pytest.mark.asyncio
async def test_timeout_cancels_operation_and_releases_slot() -> None:
    queue = WorkQueue(concurrency=1, capacity=2, timeout=0.03)
    finished = asyncio.Event()

    async def stuck():
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    with pytest.raises(DownloadTimeoutError):
        await queue.run("stuck", stuck)
    await asyncio.wait_for(finished.wait(), 1)
    assert await queue.run("next", lambda: asyncio.sleep(0, result=1)) == 1
    await queue.close()


def test_user_cooldown_and_window() -> None:
    now = [0.0]
    limiter = UserLimiter(requests=2, window=60, cooldown=3, active_limit=1, clock=lambda: now[0])
    limiter.acquire(1)
    limiter.release(1)
    with pytest.raises(RateLimitedError):
        limiter.acquire(1)
    now[0] = 4
    limiter.acquire(1)
    limiter.release(1)
    now[0] = 8
    with pytest.raises(RateLimitedError):
        limiter.acquire(1)
    limiter.acquire(2)
    limiter.release(2)
    now[0] = 61
    limiter.acquire(1)


@pytest.mark.asyncio
async def test_retry_has_three_attempts_and_backoff_but_no_permanent_retry() -> None:
    attempts = 0
    sleeps = []

    async def work():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise NetworkError("temporary")
        return 1

    async def sleep(delay):
        sleeps.append(delay)

    assert await retry_async(work, sleep=sleep, jitter=lambda: 1.0) == 1
    assert sleeps == [1.0, 2.0]
    assert attempts == 3
    attempts = 0

    async def permanent():
        nonlocal attempts
        attempts += 1
        raise InvalidURLError("permanent")

    with pytest.raises(InvalidURLError):
        await retry_async(permanent, sleep=sleep)
    assert attempts == 1


@pytest.mark.asyncio
async def test_queue_rejects_requests_beyond_bounded_capacity() -> None:
    queue = WorkQueue(concurrency=1, capacity=1, timeout=2)
    started, release = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        await release.wait()

    first = asyncio.create_task(queue.run("first", work))
    await started.wait()
    second = asyncio.create_task(queue.run("second", work))
    await asyncio.sleep(0)
    try:
        with pytest.raises(QueueFullError):
            await queue.run("third", work)
        release.set()
        await asyncio.gather(first, second)
    finally:
        await queue.close()


@pytest.mark.asyncio
async def test_retry_after_is_never_shortened() -> None:
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendDocument

    attempts, sleeps = 0, []

    async def work():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TelegramRetryAfter(method=SendDocument(chat_id=1, document="file"), message="slow down", retry_after=7)
        return True

    async def sleep(delay):
        sleeps.append(delay)

    assert await retry_async(work, sleep=sleep, jitter=lambda: 1) is True
    assert sleeps == [7, 7]


@pytest.mark.asyncio
async def test_last_waiter_cancellation_waits_for_operation_cleanup() -> None:
    queue = WorkQueue(concurrency=1, capacity=1, timeout=2)
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    task = asyncio.create_task(queue.run("owned", operation))
    await started.wait()
    task.cancel()
    try:
        await cleaning.wait()
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await queue.close()


@pytest.mark.asyncio
async def test_fresh_same_key_request_survives_previous_cancellation_cleanup() -> None:
    queue = WorkQueue(concurrency=1, capacity=2, timeout=2)
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    second_started, second_release = asyncio.Event(), asyncio.Event()

    async def abandoned():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    async def replacement():
        second_started.set()
        await second_release.wait()
        return 42

    first = asyncio.create_task(queue.run("same", abandoned))
    await started.wait()
    first.cancel()
    await cleaning.wait()
    second = asyncio.create_task(queue.run("same", replacement))
    try:
        await asyncio.sleep(0)
        assert not second.done(), "A fresh request must not inherit the cancelled future"
        release.set()
        await asyncio.gather(first, return_exceptions=True)
        await asyncio.wait_for(second_started.wait(), 1)
        follower = asyncio.create_task(queue.run("same", replacement))
        await asyncio.sleep(0)
        second_release.set()
        assert await asyncio.gather(second, follower) == [42, 42]
    finally:
        release.set()
        second_release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        await queue.close()


@pytest.mark.asyncio
async def test_cancelled_queued_request_releases_admission_without_overfilling_queue() -> None:
    queue = WorkQueue(concurrency=1, capacity=1, timeout=2)
    started, release = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        await release.wait()
        return 1

    first = asyncio.create_task(queue.run("active", work))
    await started.wait()
    abandoned = asyncio.create_task(queue.run("queued", work))
    await asyncio.sleep(0)
    abandoned.cancel()
    await asyncio.gather(abandoned, return_exceptions=True)
    replacement = asyncio.create_task(queue.run("queued", work))
    await asyncio.sleep(0)
    try:
        with pytest.raises(QueueFullError):
            await queue.run("overflow", work)
        release.set()
        assert await asyncio.gather(first, replacement) == [1, 1]
    finally:
        release.set()
        await queue.close()
