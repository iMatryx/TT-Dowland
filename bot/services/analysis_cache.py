"""Bounded, short-lived immutable extraction snapshots and single-flight loading."""

import asyncio
from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import dataclass, field
import json
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from bot.models import VideoInfo
from bot.models.exceptions import QueueFullError, VideoUnavailableError
from bot.services.extraction import Extraction, Extractor
from bot.services.files import FileRegistry
from bot.services.urls import normalize_url
from bot.utils.performance import CacheMetrics, queued_slot, scoped

MAX_METADATA_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class _Snapshot:
    video: str = field(repr=False)
    info: str = field(repr=False)
    token: str


@dataclass
class _Load:
    task: asyncio.Task
    waiters: int = 0


def _portable(value):
    """Drop extractor workspace paths; URLs/headers remain private snapshot data."""
    if isinstance(value, dict):
        return {key: _portable(item) for key, item in value.items()
                if key not in {'filepath', 'filename', '_filename', '__files_to_move', '__finaldir'}}
    if isinstance(value, list):
        return [_portable(item) for item in value]
    return value


class CachedExtractor:
    def __init__(self, source: Extractor, *, root: Path, ttl: float = 300, capacity: int = 512,
                 enabled: bool = True, concurrency: int = 3, max_pending: int = 32,
                 registry: FileRegistry | None = None, clock=time.monotonic) -> None:
        if ttl <= 0 or capacity < 1 or concurrency < 1 or max_pending < 0:
            raise ValueError('Invalid analysis cache limits')
        self.source, self.root, self.registry = source, root, registry
        self.ttl, self.capacity, self.enabled, self.clock = ttl, capacity, enabled, clock
        self.metrics = CacheMetrics('metadata')
        self._items: OrderedDict[str, tuple[float, _Snapshot]] = OrderedDict()
        self._loads: dict[str, _Load] = {}
        self._slots = asyncio.Semaphore(concurrency)
        self._max_loads = concurrency + max_pending
        self._closed = False

    def invalidate(self, url: str, token: str | None) -> None:
        key = normalize_url(url)
        item = self._items.get(key)
        # A late failure must not delete the replacement loaded by another caller.
        if item is not None and item[1].token == token:
            del self._items[key]

    async def _snapshot(self, url: str) -> tuple[_Snapshot, bool]:
        now = self.clock()
        self._items = OrderedDict((key, item) for key, item in self._items.items() if item[0] > now)
        item = self._items.get(url) if self.enabled else None
        if self.enabled:
            self.metrics.record(item is not None)
        if item is not None:
            self._items.move_to_end(url)
            return item[1], True
        async with queued_slot(self._slots, queue='extraction'):
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.analysis-cache-', dir=self.root) as temporary, (
                self.registry.track(Path(temporary)) if self.registry else nullcontext()
            ):
                extraction = await self.source.extract(url, Path(temporary))
                path = extraction.info_path
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES:
                    raise VideoUnavailableError('Missing or oversized extractor metadata.')
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                    if not isinstance(data, dict):
                        raise ValueError('Expected an extractor object')
                    snapshot = _Snapshot(extraction.video.model_dump_json(), json.dumps(_portable(data)), uuid4().hex)
                except (ValueError, TypeError, RecursionError) as exc:
                    raise VideoUnavailableError('Malformed extractor metadata.') from exc
        if self.enabled and not self._closed:
            self._items[url] = (self.clock() + self.ttl, snapshot)
            self._items.move_to_end(url)
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return snapshot, False

    @scoped
    async def extract(self, url: str, workspace: Path) -> Extraction:
        key = normalize_url(url)  # Validate even on hits; redirects are resolved by the caller.
        if self._closed:
            raise QueueFullError('Analysis cache is shutting down.')
        load = self._loads.get(key)
        if load is not None and load.task.done():
            # Old waiters may still be resuming. A completed load is no longer
            # in flight and must not resurrect an invalidated snapshot/error.
            del self._loads[key]
            load = None
        if load is None:
            if len(self._loads) >= self._max_loads:
                raise QueueFullError('Analysis capacity reached.')
            task = asyncio.create_task(self._snapshot(key), name='shared-extraction')
            task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
            load = _Load(task)
            self._loads[key] = load
        load.waiters += 1
        try:
            snapshot, cached = await asyncio.shield(load.task)
            path = workspace / 'info.json'
            path.write_text(snapshot.info, encoding='utf-8')
            return Extraction(VideoInfo.model_validate_json(snapshot.video), path,
                              cached=cached, cache_token=snapshot.token)
        finally:
            load.waiters -= 1
            if not load.waiters:
                if self._loads.get(key) is load:
                    del self._loads[key]
                if not load.task.done():
                    load.task.cancel()
                    await asyncio.gather(load.task, return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        self._items.clear()
        tasks = [load.task for load in self._loads.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loads.clear()
