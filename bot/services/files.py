"""Active-file ownership, shared artifact leases, and safe restart cleanup."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass
import fcntl
import logging
from pathlib import Path
import shutil
import threading
import time

from bot.models import DownloadResult
from bot.models.exceptions import ApplicationError

logger = logging.getLogger(__name__)


class FileRegistry:
    def __init__(self) -> None:
        self._active: dict[Path, int] = {}
        self._lock = threading.Lock()

    @contextmanager
    def track(self, path: Path) -> Iterator[None]:
        path = path.resolve()
        with self._lock:
            self._active[path] = self._active.get(path, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                self._active[path] -= 1
                if not self._active[path]:
                    del self._active[path]

    def snapshot(self) -> set[Path]:
        with self._lock:
            return set(self._active)

    def _busy(self, scope: Path, ignored: Path | None = None) -> bool:
        return any(count - (1 if path == ignored else 0) > 0
                   and (path == scope or path in scope.parents or scope in path.parents)
                   for path, count in self._active.items())

    def cleanup_if_idle(self, result: DownloadResult, *, retention_hold: bool = False) -> bool:
        path = result.file_path.resolve()
        scope = path.parent if result.photos else path
        with self._lock:
            if self._busy(scope, path if retention_hold else None):
                return False
            result.cleanup()
            return True

    def remove_abandoned(self, path: Path, *, threshold: float) -> bool:
        with self._lock:
            if path.is_symlink() or self._busy(path) or path.stat().st_mtime >= threshold:
                return False
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            return True


class InstanceLock:
    """Prevent two bot runtimes from cleaning or writing the same workspace."""

    def __init__(self, root: Path) -> None:
        self.root, self._file = root, None

    def acquire(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        file = (self.root / ".runtime.lock").open("a")
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            file.close()
            raise ApplicationError("Another bot runtime owns this workspace.") from exc
        self._file = file

    def release(self) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None


class FileCleaner:
    def __init__(self, root: Path, registry: FileRegistry, *, max_age: float = 3600) -> None:
        self.root, self.registry, self.max_age = root.resolve(), registry, max_age

    async def sweep(self, *, recovery: bool = False) -> int:
        def clean() -> int:
            count = 0
            if not self.root.exists():
                return count
            threshold = time.time() - (0 if recovery else self.max_age)
            for path in self.root.iterdir():
                if not path.name.startswith((".tiktok-", ".analysis-", "tiktok-")) or path.is_symlink():
                    continue
                try:
                    if self.registry.remove_abandoned(path, threshold=threshold):
                        count += 1
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning("Abandoned-file cleanup failed")
            return count

        removed = await asyncio.to_thread(clean)
        logger.info("Cleanup removed=%d recovery=%s", removed, recovery)
        return removed


@dataclass(eq=False)
class _Artifact:
    task: asyncio.Task
    users: int = 0
    expires_at: float | None = None
    timer: asyncio.TimerHandle | None = None
    hold: AbstractContextManager | None = None
    key: str = ''
    order: int = 0
    removed: bool = False


class SharedDownloads:
    def __init__(self, registry: FileRegistry, *, max_bytes: int = 512 * 1024 * 1024, clock: Callable[[], float] = time.monotonic) -> None:
        if max_bytes < 0:
            raise ValueError('Retention byte limit must be nonnegative')
        self.registry = registry
        self.max_bytes = max_bytes
        self.clock = clock
        self._order = 0
        self._retry_timer: asyncio.TimerHandle | None = None
        self._items: dict[str, _Artifact] = {}
        self._retired: set[_Artifact] = set()
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._closed = False

    def retain(self, key: str, *, seconds: float, result: DownloadResult | None = None) -> bool:
        """Keep an already verified original until a fixed monotonic deadline."""
        if seconds <= 0:
            raise ValueError('Retention must be positive')
        item = self._items.get(key)
        if not self._closed and item is None and result is not None:
            # An upload can finish after the previous retention window expires.
            # Reattach that exact live lease, never a newer replacement's key.
            item = next((candidate for candidate in self._retired
                         if candidate.key == key and candidate.users > 0 and candidate.task.result() is result), None)
            if item is not None:
                self._retired.remove(item)
                self._items[key] = item
        if (self._closed or item is None or not item.task.done() or item.task.cancelled()
                or item.task.exception() is not None):
            return False
        if item.users == 0 and item.expires_at is not None and item.expires_at <= self.clock():
            return False
        artifact = item.task.result()
        if result is not None and artifact is not result:
            return False
        self._trim()
        if self.max_bytes == 0 or self._bytes() > self.max_bytes or self._items.get(key) is not item:
            self._release_retention(item)
            logger.info('Original retention declined limit_bytes=%d', self.max_bytes)
            return False
        if item.hold is None:
            item.hold = self.registry.track(artifact.file_path)
            item.hold.__enter__()
        self._cancel_timer(item)
        item.expires_at = self.clock() + seconds
        item.timer = asyncio.get_running_loop().call_later(seconds, self._start_expiry, key, item)
        return True

    def _ready(self, item: _Artifact) -> bool:
        return (not item.removed and item.task.done() and not item.task.cancelled()
                and item.task.exception() is None)

    def _bytes(self) -> int:
        paths = set()
        for item in set(self._items.values()) | self._retired:
            if self._ready(item):
                result = item.task.result()
                paths.add(result.file_path)
                paths.update(photo.file_path for photo in result.photos)
        total = 0
        for path in paths:
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                pass
            except OSError:
                return self.max_bytes + 1  # Unknown size cannot authorize more retention.
        return total

    def _forget(self, item: _Artifact) -> None:
        if self._items.get(item.key) is item:
            self._items.pop(item.key)
        self._retired.discard(item)
        self._release_retention(item)

    def _trim(self) -> None:
        # Called synchronously on the event loop: admission and victim selection
        # cannot interleave. Unlink verified idle originals, never active leases.
        for item in sorted(set(self._items.values()) | self._retired, key=lambda item: item.order):
            if self._bytes() <= self.max_bytes:
                break
            if item.users or not self._ready(item):
                continue
            try:
                removed = self.registry.cleanup_if_idle(item.task.result(), retention_hold=item.hold is not None)
            except OSError:
                logger.warning('Original eviction deferred')
                self._schedule_retry()
                continue
            if removed:
                item.removed = True
                self._forget(item)
                logger.info('Oldest eligible original evicted limit_bytes=%d', self.max_bytes)
            else:
                self._schedule_retry()

    def _spawn_maintenance(self) -> None:
        if self._closed:
            return
        task = asyncio.create_task(self.expire(), name='retained-original-maintenance')
        self._cleanup_tasks.add(task)
        def finished(task):
            self._cleanup_tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:
                logger.warning('Original maintenance deferred')
        task.add_done_callback(finished)

    def _retry_maintenance(self) -> None:
        self._retry_timer = None
        self._spawn_maintenance()

    def _schedule_retry(self) -> None:
        if not self._closed and self._retry_timer is None:
            self._retry_timer = asyncio.get_running_loop().call_later(1, self._retry_maintenance)

    def _start_expiry(self, key: str, item: _Artifact) -> None:
        item.timer = None
        if self._closed or self._items.get(key) is not item:
            return
        remaining = (item.expires_at or 0) - self.clock()
        if remaining > 0:
            item.timer = asyncio.get_running_loop().call_later(remaining, self._start_expiry, key, item)
            return
        self._spawn_maintenance()

    @staticmethod
    def _cancel_timer(item: _Artifact) -> None:
        if item.timer is not None:
            item.timer.cancel()
            item.timer = None

    def _release_retention(self, item: _Artifact) -> None:
        self._cancel_timer(item)
        item.expires_at = None
        if item.hold is not None:
            item.hold.__exit__(None, None, None)
            item.hold = None

    async def expire(self) -> None:
        """Retire expired entries, enforce byte budget, and retry deferred cleanup."""
        now = self.clock()
        for key, item in list(self._items.items()):
            if item.expires_at is not None and item.expires_at <= now:
                self._items.pop(key)
                self._release_retention(item)
                self._retired.add(item)
        self._trim()
        for item in list(self._retired):
            if not item.users:
                if await self._remove_result(item):
                    self._retired.discard(item)
                else:
                    self._schedule_retry()

    @asynccontextmanager
    async def acquire(self, key: str, download: Callable[[], Awaitable[DownloadResult]]) -> AsyncIterator[DownloadResult]:
        if self._closed:
            raise ApplicationError('Shared downloads are closed')
        await self.expire()
        if self._closed:
            raise ApplicationError('Shared downloads are closed')
        item = self._items.get(key)
        if item is None:
            task = asyncio.create_task(download(), name="shared-video-download")
            task.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
            self._order += 1
            item = _Artifact(task, key=key, order=self._order)
            self._items[key] = item
        item.users += 1
        try:
            result = await asyncio.shield(item.task)
            with self.registry.track(result.file_path):
                yield result
        finally:
            item.users -= 1
            self._trim()
            retained = item.expires_at is not None and item.expires_at > self.clock() and not self._closed
            if item.users == 0 and not retained:
                if self._items.get(key) is item:
                    self._items.pop(key)
                self._retired.add(item)
                self._release_retention(item)
                if not item.task.done() and not item.task.cancelling():
                    item.task.cancel()
                await asyncio.gather(item.task, return_exceptions=True)
                if await self._remove_result(item):
                    self._retired.discard(item)
                else:
                    self._schedule_retry()

    async def _remove_result(self, item: _Artifact) -> bool:
        if item.removed:
            return True
        if not item.task.done():
            return False
        if not item.task.cancelled() and item.task.exception() is None:
            try:
                removed = await asyncio.to_thread(self.registry.cleanup_if_idle, item.task.result())
                if not removed:
                    return False
                logger.info('Completed download file removed')
            except OSError:
                logger.warning('Completed-file cleanup deferred')
                return False
        item.removed = True
        return True

    async def close(self) -> None:
        self._closed = True
        if self._retry_timer is not None:
            self._retry_timer.cancel()
            self._retry_timer = None
        items = set(self._items.values()) | self._retired
        self._items.clear()
        self._retired.update(items)
        for item in items:
            self._release_retention(item)
        await asyncio.gather(*self._cleanup_tasks, return_exceptions=True)
        for item in items:
            if not item.users:
                if not item.task.done() and not item.task.cancelling():
                    item.task.cancel()
                await asyncio.gather(item.task, return_exceptions=True)
                if await self._remove_result(item):
                    self._retired.discard(item)
