"""Active-file ownership, shared artifact leases, and safe restart cleanup."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
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
        active = self.registry.snapshot()

        def clean() -> int:
            count = 0
            if not self.root.exists():
                return count
            threshold = time.time() - (0 if recovery else self.max_age)
            for path in self.root.iterdir():
                if not path.name.startswith((".tiktok-", ".analysis-", "tiktok-")) or path.is_symlink():
                    continue
                if any(path == item or path in item.parents or item in path.parents for item in active):
                    continue
                try:
                    if path.stat().st_mtime >= threshold:
                        continue
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    count += 1
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning("Abandoned-file cleanup failed")
            return count

        removed = await asyncio.to_thread(clean)
        logger.info("Cleanup removed=%d recovery=%s", removed, recovery)
        return removed


@dataclass
class _Artifact:
    task: asyncio.Task
    users: int = 0


class SharedDownloads:
    def __init__(self, registry: FileRegistry) -> None:
        self.registry = registry
        self._items: dict[str, _Artifact] = {}

    @asynccontextmanager
    async def acquire(self, key: str, download: Callable[[], Awaitable[DownloadResult]]) -> AsyncIterator[DownloadResult]:
        item = self._items.get(key)
        if item is None:
            task = asyncio.create_task(download(), name="shared-video-download")
            task.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
            item = _Artifact(task)
            self._items[key] = item
        item.users += 1
        try:
            result = await asyncio.shield(item.task)
            with self.registry.track(result.file_path):
                yield result
        finally:
            item.users -= 1
            if item.users == 0:
                self._items.pop(key, None)
                if not item.task.done():
                    item.task.cancel()
                await asyncio.gather(item.task, return_exceptions=True)
                await self._remove_result(item.task)

    async def _remove_result(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is None:
            try:
                await asyncio.to_thread(task.result().file_path.unlink, missing_ok=True)
                logger.info("Completed download file removed")
            except OSError:
                logger.warning("Completed-file cleanup deferred")

    async def close(self) -> None:
        items = list(self._items.values())
        for item in items:
            if not item.task.done():
                item.task.cancel()
        await asyncio.gather(*(item.task for item in items), return_exceptions=True)
        for item in items:
            await self._remove_result(item.task)
        self._items.clear()
