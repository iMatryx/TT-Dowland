"""Telegram adapter for analysis and ephemeral, owner-bound download actions."""

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import secrets
import tempfile
import time
from contextlib import AsyncExitStack, nullcontext

from bot.models import DownloadResult, VideoInfo
from bot.models.exceptions import ApplicationError, DownloadTimeoutError, FileSizeLimitError
from bot.services.downloader import TikTokDownloader
from bot.services.quality import select_formats
from bot.services.urls import resolve_url
from bot.utils.network import public_http_client


@dataclass
class PendingDownload:
    key: str
    user_id: int
    chat_id: int
    message_id: int
    info: VideoInfo
    expires_at: float
    busy: bool = False


class PendingRequests:
    """Bounded action registry, not a file cache; callbacks never contain URLs."""

    def __init__(self, *, ttl: float = 600, capacity: int = 512,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if ttl <= 0 or capacity < 1:
            raise ValueError("ttl and capacity must be positive")
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self._items: OrderedDict[str, PendingDownload] = OrderedDict()

    def _prune(self) -> None:
        now = self.clock()
        for key, item in list(self._items.items()):
            if item.expires_at <= now and not item.busy:
                del self._items[key]

    def add(self, user_id: int, chat_id: int, message_id: int, info: VideoInfo) -> PendingDownload:
        self._prune()
        if len(self._items) >= self.capacity:
            oldest = next((key for key, item in self._items.items() if not item.busy), None)
            if oldest is None:
                raise ApplicationError("All download action slots are busy.")
            del self._items[oldest]
        key = secrets.token_urlsafe(12)
        item = PendingDownload(key, user_id, chat_id, message_id, info, self.clock() + self.ttl)
        self._items[key] = item
        return item

    def get(self, key: str) -> PendingDownload | None:
        self._prune()
        return self._items.get(key)

    def remove(self, key: str) -> None:
        self._items.pop(key, None)


class TelegramDownloadService:
    """Reuse extraction and download; revalidate source availability on download."""

    def __init__(self, engine: TikTokDownloader) -> None:
        self.engine = engine
        self._analysis_slots = asyncio.Semaphore(engine.settings.max_concurrent_downloads)

    async def analyze(self, url: str) -> VideoInfo:
        try:
            async with asyncio.timeout(90):
                async with self._analysis_slots:
                    async with AsyncExitStack() as stack:
                        client = getattr(self.engine, "client", None)
                        if client is None:
                            client = await stack.enter_async_context(public_http_client())
                        canonical = await resolve_url(url, client)
                    base = self.engine.settings.download_dir.expanduser().resolve()
                    base.mkdir(parents=True, exist_ok=True)
                    registry = getattr(self.engine, "registry", None)
                    with tempfile.TemporaryDirectory(prefix=".analysis-", dir=base) as temporary, (
                        registry.track(Path(temporary)) if registry else nullcontext()
                    ):
                        extraction = await self.engine.extractor.extract(canonical, Path(temporary))
                        selection = select_formats(extraction.video.formats)
                        if selection.known_size_bytes > self.engine.settings.max_file_size_mb * 1024 * 1024:
                            raise FileSizeLimitError("Selected original exceeds the local download limit.")
                        return extraction.video
        except TimeoutError as exc:
            raise DownloadTimeoutError("Analysis deadline exceeded.") from exc

    async def download(self, url: str) -> DownloadResult:
        return await self.engine.download(url)
