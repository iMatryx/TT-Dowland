"""Cloud upload policy and disk-streaming Telegram delivery without conversion."""

from typing import ClassVar, Literal, Protocol
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramEntityTooLarge
from aiogram.types import FSInputFile, Message

from bot.models import DownloadResult, VideoFormat, VideoInfo
from bot.models.exceptions import CorruptFileError, FileSizeLimitError, TelegramDeliveryError, InvalidCachedFileError
from bot.services.cache import CacheEntry
from bot.services.operations import retry_async
from bot.utils.telegram_text import video_details

SendMode = Literal["video", "document"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SentMedia:
    kind: SendMode
    file_id: str | None


class TelegramUploadPolicy(Protocol):
    """Future Local Bot API integration supplies its policy and client separately."""

    @property
    def max_upload_bytes(self) -> int: ...


class CloudUploadPolicy:
    """Conservative decimal cap for the documented standard 50 MB upload limit."""

    max_upload_bytes: ClassVar[int] = 50_000_000


def video_compatible(extension: str, verified: VideoFormat | None) -> bool:
    return bool(verified is not None and extension.lower().lstrip(".") == "mp4"
                and verified.video_codec in {"h264", "avc1"}
                and (verified.has_audio is False or verified.audio_codec in {"aac", "mp4a", "none"}))


class TelegramSender:
    def __init__(self, policy: TelegramUploadPolicy | None = None, *, timeout: float = 120) -> None:
        self.policy = policy or CloudUploadPolicy()
        self.timeout = timeout

    def check_size(self, size: int) -> None:
        if size > self.policy.max_upload_bytes:
            raise FileSizeLimitError("File exceeds the Telegram upload policy.")

    async def send(self, bot: Bot, chat_id: int, result: DownloadResult, mode: SendMode,
                   *, message_thread_id: int | None = None) -> SendMode:
        sent = await self.send_with_receipt(bot, chat_id, result, mode, message_thread_id=message_thread_id)
        return sent.kind

    async def send_with_receipt(self, bot: Bot, chat_id: int, result: DownloadResult, mode: SendMode,
                                *, message_thread_id: int | None = None) -> SentMedia:
        path = result.file_path
        if path.is_symlink() or not path.is_file():
            raise CorruptFileError("The verified download is no longer available.")
        size = path.stat().st_size
        self.check_size(size)
        if size <= 0 or size != result.file_size_bytes:
            raise CorruptFileError("The download changed after verification.")
        verified = result.verified_format
        compatible = video_compatible(path.suffix, verified)
        actual_mode: SendMode = "video" if mode == "video" and compatible else "document"
        caption = video_details(result.video_info, verified, result.format, size)
        if mode == "video" and actual_mode == "document":
            caption += "\nℹ️ Format incompatibil ca video; trimit originalul ca document."
        kwargs = dict(chat_id=chat_id, caption=caption, parse_mode="HTML")
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        try:
            if actual_mode == "video":
                assert verified is not None
                response = await self._request(lambda: bot.send_video(
                    video=FSInputFile(path), width=verified.width, height=verified.height, **kwargs), bot=bot)
            else:
                response = await self._request(lambda: bot.send_document(document=FSInputFile(path), **kwargs), bot=bot)
        except TelegramEntityTooLarge as exc:
            raise FileSizeLimitError("Telegram rejected the upload size.") from exc
        except TelegramBadRequest as exc:
            if any(text in exc.message.lower() for text in ("file is too big", "file too large", "entity too large")):
                raise FileSizeLimitError("Telegram rejected the upload size.") from exc
            raise TelegramDeliveryError("Telegram rejected the media upload.") from exc
        except TelegramAPIError as exc:
            logger.warning("Telegram upload error=%s", type(exc).__name__)
            raise TelegramDeliveryError("Telegram media delivery failed.") from exc
        media = getattr(response, actual_mode, None)
        file_id = getattr(media, "file_id", None)
        return SentMedia(actual_mode, file_id if isinstance(file_id, str) and file_id else None)

    async def _request(self, operation: Callable[[], Awaitable[Message]], *, bot: Bot) -> Message:
        try:
            async with asyncio.timeout(self.timeout):
                if getattr(getattr(bot, "session", None), "_retry_managed", False) is True:
                    return await operation()
                return await retry_async(operation, retry_network=False)
        except TimeoutError as exc:
            raise TelegramDeliveryError("Telegram send timed out; delivery status is uncertain.") from exc

    async def send_cached(self, bot: Bot, chat_id: int, entry: CacheEntry, info: VideoInfo,
                          *, message_thread_id: int | None = None) -> None:
        self.check_size(entry.size)
        if entry.kind == "document" and not entry.original:
            raise InvalidCachedFileError("Cached media is not an original document.")
        kwargs = dict(chat_id=chat_id, caption=video_details(info, entry.verified, entry.source, entry.size), parse_mode="HTML")
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        try:
            if entry.kind == "video":
                await self._request(lambda: bot.send_video(video=entry.file_id, **kwargs), bot=bot)
            else:
                await self._request(lambda: bot.send_document(document=entry.file_id, **kwargs), bot=bot)
        except TelegramBadRequest as exc:
            if any(text in exc.message.lower() for text in ("file_id", "file identifier", "file reference", "wrong remote file")):
                raise InvalidCachedFileError("Telegram rejected a cached file identifier.") from exc
            raise TelegramDeliveryError("Telegram rejected the cached media send.") from exc
        except TelegramAPIError as exc:
            logger.warning("Telegram cached-send error=%s", type(exc).__name__)
            raise TelegramDeliveryError("Telegram cached media delivery failed.") from exc
