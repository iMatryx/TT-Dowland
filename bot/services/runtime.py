"""Bot lifecycle: bounded work, shared downloads, persistent cache and recovery."""

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging

from bot.utils.i18n import tr
import httpx
from aiogram import Bot

from bot.config import Settings
from bot.services.restart import RestartController
from bot.models import VideoInfo
from bot.models.exceptions import InvalidCachedFileError, TelegramDeliveryError, QueueFullError, DownloadTimeoutError
from bot.services.conversion import ConversionLimiter
from bot.services.analysis_cache import CachedExtractor
from bot.services.cache import CacheEntry, SQLiteCache
from bot.utils.performance import CacheMetrics, scoped
from bot.services.files import FileCleaner, FileRegistry, InstanceLock, SharedDownloads
from bot.services.operations import UserLimiter, WorkQueue, retry_async
from bot.services.quality import FormatSelection, select_audio, select_formats, select_photos
from bot.services.telegram_sender import TelegramSender, SendMode, video_compatible, resolve_delivery_mode
from bot.services.telegram_download import TelegramDownloadService
from bot.utils.telegram_progress import ProgressMessage
from bot.utils.network import public_http_client

logger = logging.getLogger(__name__)
ORIGINAL_RETENTION_SECONDS = 180


def quality_key(info: VideoInfo, mode: str = "video") -> str:
    if info.media_type == "photo":
        info = select_photos(info)
        data = [str(info.webpage_url), [(p.width, p.height, p.has_watermark, p.watermark_evidence) for p in info.photos]]
        return hashlib.sha256(json.dumps(data).encode()).hexdigest()
    selected = (FormatSelection(select_audio(info.formats)) if mode == 'audio'
                else select_formats(info.formats))
    fields = ("format_id", "ext", "width", "height", "fps", "tbr", "video_codec", "audio_codec",
              "has_watermark", "has_audio", "has_video")
    data = {"url": str(info.webpage_url), "audio_only": mode == 'audio',
            "preview": mode == 'preview',
            "audio_quality": 'high' if mode == 'audio' else None,
            "video": {name: getattr(selected.video, name) for name in fields},
            "audio": {name: getattr(selected.audio, name) for name in fields} if selected.audio else None}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@dataclass
class _Receipt:
    entry: CacheEntry | None
    owner: object | None


class BotRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.restart = RestartController(settings.administrator_ids, enabled=settings.restart_enabled)
        self.work_dir = settings.download_dir.expanduser().resolve() / "bot-work"
        self.extraction_caches: set[CachedExtractor] = set()
        self.telegram_metrics = CacheMetrics("telegram")
        self.registry = FileRegistry()
        self.files = SharedDownloads(self.registry, max_bytes=settings.max_retained_originals_mb * 1024 * 1024)
        self.cleaner = FileCleaner(self.work_dir, self.registry, max_age=settings.abandoned_file_seconds)
        self.lock = InstanceLock(self.work_dir)
        self.queue = WorkQueue(concurrency=settings.max_concurrent_downloads,
                               capacity=settings.max_pending_requests, timeout=settings.operation_timeout_seconds)
        # Admit bounded deliveries independently of scarce download/encoding slots.
        self.delivery_queue = WorkQueue(
            concurrency=settings.max_concurrent_downloads + settings.max_pending_requests, capacity=0,
            timeout=settings.operation_timeout_seconds, name="delivery")
        self.conversions = ConversionLimiter(settings.max_concurrent_conversions)
        self.limiter = UserLimiter(requests=settings.user_requests_per_minute,
                                   cooldown=settings.user_cooldown_seconds, active_limit=settings.max_user_operations)
        self.cache = SQLiteCache(settings.cache_db_path, ttl=settings.cache_ttl_seconds) if settings.cache_enabled else None
        self.media_details = self.cache if self.cache is not None else SQLiteCache(settings.cache_db_path, ttl=settings.cache_ttl_seconds)
        self.client: httpx.AsyncClient | None = None
        self.handlers: set[asyncio.Task] = set()
        self.closing = False
        self._cleaner_task: asyncio.Task | None = None

    async def start(self) -> None:
        await asyncio.to_thread(self.lock.acquire)
        try:
            await self.cleaner.sweep(recovery=True)
            await self.media_details.open()
            self.client = public_http_client(limit=self.settings.http_max_connections)
            self._cleaner_task = asyncio.create_task(self._clean_loop(), name="abandoned-file-cleaner")
            logger.info("Bot runtime started concurrency=%d queue_capacity=%d", self.settings.max_concurrent_downloads,
                        self.settings.max_pending_requests)
        except BaseException:
            await self.close()
            raise

    async def _clean_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.cleanup_interval_seconds)
            try:
                await self.cleaner.sweep()
            except OSError:
                logger.warning("Periodic cleanup failed")

    @scoped
    async def analyze(self, service: TelegramDownloadService, url: str) -> VideoInfo:
        if self.closing:
            raise QueueFullError("Service is shutting down")

        info = await self.queue.run("analysis:" + url, lambda: retry_async(lambda: service.analyze(url)))
        return info.model_copy(deep=True)

    async def _cached(self, bot_id: int, info: VideoInfo, mode: SendMode, *, record: bool = True) -> CacheEntry | None:
        if self.cache is None:
            return None
        try:
            for kind in (("video",) if mode == "preview" else ("audio",) if mode == "audio" else ("zip",) if mode == "zip" else ("document",) if mode == "document" else ("video", "document")):
                entry = await self.cache.get(bot_id, info.id, quality_key(info, mode), kind)
                if entry is None and mode == "preview":
                    # An original playable upload is also a valid preview. Processed
                    # previews retain their own key and can never become originals.
                    candidate = await self.cache.get(bot_id, info.id, quality_key(info, "video"), "video")
                    verified = candidate.verified if candidate else None
                    if (verified is not None and not verified.remuxed and not verified.transcoded
                            and video_compatible(verified.ext, verified)):
                        entry = candidate
                if entry is not None:
                    if mode == "video" and kind == "document" and video_compatible(
                        entry.verified.ext if entry.verified else "", entry.verified,
                    ):
                        continue
                    if entry.size > self.settings.max_file_size_mb * 1024 * 1024:
                        continue
                    if record:
                        self.telegram_metrics.record(True)
                    return entry
        except Exception as exc:
            logger.warning("Cache lookup failed error=%s", type(exc).__name__)
        if record:
            self.telegram_metrics.record(False)
        return None

    async def _invalidate(self, entry: CacheEntry) -> None:
        if self.cache is not None:
            try:
                await self.cache.invalidate(entry)
            except Exception as exc:
                logger.warning("Cache invalidation failed error=%s", type(exc).__name__)

    @scoped
    async def deliver(self, bot: Bot, chat_id: int, info: VideoInfo, mode: SendMode, service: TelegramDownloadService,
                      sender: TelegramSender, progress: ProgressMessage, *, message_thread_id: int | None = None,
                      owner_id: int | None = None) -> None:
        mode = resolve_delivery_mode(info, mode)
        try:
            async with asyncio.timeout(self.settings.operation_timeout_seconds):
                await self._deliver(bot, chat_id, info, mode, service, sender, progress,
                                    message_thread_id=message_thread_id, owner_id=owner_id)
        except TimeoutError as exc:
            raise DownloadTimeoutError("Delivery operation deadline exceeded.") from exc

    async def _deliver(self, bot: Bot, chat_id: int, info: VideoInfo, mode: SendMode, service: TelegramDownloadService,
                      sender: TelegramSender, progress: ProgressMessage, *, message_thread_id: int | None = None,
                      _refresh: bool = False, owner_id: int | None = None) -> None:
        async def download_original():
            return await service.download(str(info.webpage_url), **({'audio_only': True} if mode == 'audio' else {}))

        if info.media_type == "photo":
            # File-id caching of Telegram previews would lose original image bytes.
            # Lease the verified originals across concurrent recipients instead.
            async def photo_upload():
                async with self.files.acquire(
                    f"photo:{info.id}:{quality_key(info, mode)}",
                    download_original,
                ) as result:
                    await progress.update(tr('📤 Trimit fotografiile originale...'))
                    await sender.send(bot, chat_id, result, mode, message_thread_id=message_thread_id)
            # A delivery is per user; coalescing uploads would omit recipients.
            import secrets
            await self.delivery_queue.run("photos:" + secrets.token_urlsafe(12), photo_upload)
            return
        owner = object()

        async def status(text: str) -> None:
            from aiogram.exceptions import TelegramAPIError

            try:
                await progress.update(text)
            except TelegramAPIError:
                logger.warning("Could not edit delivery progress")

        async def send_cached(entry: CacheEntry) -> None:
            await status(tr('📤 Pregătesc trimiterea...'))
            await sender.send_cached(bot, chat_id, entry, info, message_thread_id=message_thread_id,
                                     **({'owner_id': owner_id} if owner_id is not None else {}))
            if mode == 'preview':
                original_key = f"{bot.id}:{info.id}:{quality_key(info, 'video')}"
                self.files.retain(original_key, seconds=ORIGINAL_RETENTION_SECONDS)

        if self.closing:
            raise QueueFullError("Service is shutting down")
        entry = None if _refresh else await self._cached(bot.id, info, mode, record=False)
        if entry is not None:
            self.telegram_metrics.record(True)
            try:
                await send_cached(entry)
                return
            except InvalidCachedFileError:
                await self._invalidate(entry)
                _refresh = True

        async def upload() -> _Receipt:
            cached = None if _refresh else await self._cached(bot.id, info, mode)
            if cached is not None:
                return _Receipt(cached, None)
            await status(tr('📥 Descarc videoclipul...'))
            key = f"{bot.id}:{info.id}:{quality_key(info, 'video' if mode == 'preview' else mode)}"
            async with self.files.acquire(key, download_original) as result:
                await status(tr('📤 Pregătesc trimiterea...'))
                sent = await sender.send_with_receipt(bot, chat_id, result, mode, message_thread_id=message_thread_id,
                    **({'owner_id': owner_id} if owner_id is not None else {}))
                if mode == 'preview':
                    self.files.retain(key, seconds=ORIGINAL_RETENTION_SECONDS, result=result)
                record = None
                if sent.file_id:
                    record = CacheEntry(
                        bot_id=bot.id, video_id=result.video_info.id, url=str(result.video_info.webpage_url),
                        quality=quality_key(result.video_info, mode), kind=sent.kind, file_id=sent.file_id,
                        size=sent.file_size_bytes if sent.file_size_bytes is not None else result.file_size_bytes, source=result.format,
                        verified=sent.verified_format or result.verified_format,
                        original=sent.kind in {"document", "zip"},
                    )
                    if self.cache is not None:
                        try:
                            await self.cache.put(record)
                        except Exception as exc:
                            logger.warning("Cache write failed after successful delivery error=%s", type(exc).__name__)
                return _Receipt(record, owner)

        key = f'delivery:{bot.id}:{info.id}:{quality_key(info, mode)}:{mode}:{chat_id}:{message_thread_id}'
        receipt = await self.delivery_queue.run(key, upload)
        if receipt.owner is not owner:
            if receipt.entry is None:
                raise TelegramDeliveryError("No reusable file identifier was returned.")
            try:
                await send_cached(receipt.entry)
            except InvalidCachedFileError:
                if _refresh:
                    raise
                if self.cache is not None:
                    await self._invalidate(receipt.entry)
                await self._deliver(bot, chat_id, info, mode, service, sender, progress,
                                    message_thread_id=message_thread_id, _refresh=True, owner_id=owner_id)

    async def close(self) -> None:
        self.closing = True
        current = asyncio.current_task()
        tasks = [task for task in self.handlers if task is not current and not task.done()]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.settings.shutdown_grace_seconds)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._cleaner_task is not None:
            self._cleaner_task.cancel()
            await asyncio.gather(self._cleaner_task, return_exceptions=True)
            self._cleaner_task = None
        for close in (self.delivery_queue.close, self.queue.close, self.files.close,
                      *(cache.close for cache in self.extraction_caches),
                      *([self.client.aclose] if self.client is not None else []),
                      self.media_details.close):
            try:
                await close()
            except Exception as exc:
                logger.error("Resource close failed error=%s", type(exc).__name__)
        self.client = None
        self.extraction_caches.clear()
        await asyncio.to_thread(self.lock.release)
        logger.info("Bot runtime stopped")
