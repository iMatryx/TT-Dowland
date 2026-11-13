"""Telegram delivery with verified, bounded preparation of playable previews."""

from typing import ClassVar, Literal, Protocol, TypeVar
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
import asyncio
import logging
from pathlib import Path
from contextlib import nullcontext

from bot.utils.performance import scoped, stage
import tempfile
import zipfile

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramEntityTooLarge
from aiogram.types import FSInputFile, Message, InputMediaPhoto, InputMediaDocument

from bot.models import DownloadResult, VideoFormat, VideoInfo
from bot.models.exceptions import CorruptFileError, FileSizeLimitError, TelegramDeliveryError, InvalidCachedFileError
from bot.services.cache import CacheEntry, SQLiteCache
from bot.handlers.keyboards import delivered_video_keyboard, media_info_keyboard
from bot.services.media_info import audio_captions, media_title, video_captions, photo_captions, zip_captions, video_zip_captions
from bot.services.operations import retry_async
from bot.services.conversion import ConversionLimiter
from bot.services.media import verification_matches
from bot.utils.telegram_text import video_details

SendMode = Literal["chat", "file", "video", "document", "maximum", "clean", "clean_document", "photos", "album", "zip", "audio", "preview"]
logger = logging.getLogger(__name__)
T = TypeVar("T")


def resolve_delivery_mode(info: VideoInfo, mode: str) -> str:
    if mode == "chat":
        return "album" if info.media_type == "photo" else "video"
    if mode == "file":
        return "photos" if info.media_type == "photo" else "document"
    return mode


@dataclass(frozen=True)
class SentMedia:
    kind: SendMode
    file_id: str | None
    file_size_bytes: int | None = None
    verified_format: VideoFormat | None = None


class TelegramUploadPolicy(Protocol):
    """Future Local Bot API integration supplies its policy and client separately."""

    @property
    def max_upload_bytes(self) -> int: ...


class CloudUploadPolicy:
    """Conservative decimal cap for the documented standard 50 MB upload limit."""

    max_upload_bytes: ClassVar[int] = 50_000_000


@dataclass(frozen=True)
class LocalUploadPolicy:
    max_upload_bytes: int = 2_000_000_000


def video_compatible(extension: str, verified: VideoFormat | None) -> bool:
    return bool(verified is not None and extension.lower().lstrip(".") == "mp4"
                and verified.video_codec in {"h264", "avc1"}
                and verified.pixel_format in {None, "yuv420p", "yuvj420p"}
                and (verified.container is None or "mp4" in verified.container.split(","))
                and (verified.has_audio is False or verified.audio_codec in {"aac", "mp4a", "none"}))


class TelegramSender:
    def __init__(self, policy: TelegramUploadPolicy | None = None, *, timeout: float = 120, details: SQLiteCache | None = None, pending=None, conversions: ConversionLimiter | None = None) -> None:
        self.policy = policy or CloudUploadPolicy()
        self.timeout = timeout
        self.details = details
        self.pending = pending
        self.conversions = conversions or ConversionLimiter()

    def check_size(self, size: int) -> None:
        if size > self.policy.max_upload_bytes:
            raise FileSizeLimitError("File exceeds the Telegram upload policy.")

    async def send(self, bot: Bot, chat_id: int, result: DownloadResult, mode: SendMode,
                   *, message_thread_id: int | None = None, owner_id: int | None = None, preferences=None) -> SendMode:
        mode = resolve_delivery_mode(result.video_info, mode)
        if result.video_info.media_type == "photo":
            await self.send_photos(bot, chat_id, result, mode, message_thread_id=message_thread_id)
            return mode
        sent = await self.send_with_receipt(bot, chat_id, result, mode, message_thread_id=message_thread_id,
                                            owner_id=owner_id, preferences=preferences)
        return sent.kind

    @scoped
    async def send_with_receipt(self, bot: Bot, chat_id: int, result: DownloadResult, mode: SendMode,
                                *, message_thread_id: int | None = None, owner_id: int | None = None, preferences=None) -> SentMedia:
        mode = resolve_delivery_mode(result.video_info, mode)
        if result.video_info.media_type != "video" or mode not in {"video", "document", "maximum", "clean", "clean_document", "zip", "audio", "preview"}:
            raise TelegramDeliveryError("Media kind changed; analyze the post again.")
        path = result.file_path
        if path.is_symlink() or not path.is_file():
            raise CorruptFileError("The verified download is no longer available.")
        size = path.stat().st_size
        self.check_size(size)
        if size <= 0 or size != result.file_size_bytes:
            raise CorruptFileError("The download changed after verification.")
        if result.verified_format is not None and result.verified_format._file_identity is not None:
            if not verification_matches(path, result.verified_format):
                raise CorruptFileError("The download changed after verification.")
        if mode == "audio":
            return await self._send_audio(bot, chat_id, result, message_thread_id=message_thread_id)
        if result.audio_only:
            raise CorruptFileError("Audio cannot be sent as an original video.")
        if mode == "zip":
            return await self._send_video_zip(bot, chat_id, result, message_thread_id=message_thread_id)
        verified = result.verified_format
        compatible = video_compatible(path.suffix, verified)
        if mode == 'preview' and not compatible:
            return await self._send_preview(bot, chat_id, result, message_thread_id=message_thread_id,
                                            owner_id=owner_id, preferences=preferences)
        actual_mode: SendMode = "video" if mode in {"video", "maximum", "clean", "preview"} and compatible else "document"
        caption = video_details(result.video_info, verified, result.format, size)
        if mode in {"video", "maximum", "clean"} and actual_mode == "document":
            caption += "\nℹ️ Format incompatibil ca video; trimit originalul ca document."
        captions = video_captions(result.video_info, verified, result.format, size, document=actual_mode == "document") if self.details is not None else None
        kwargs = dict(chat_id=chat_id, caption=captions[0] if captions else caption, parse_mode="HTML")
        if captions:
            kwargs["reply_markup"] = media_info_keyboard()
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        try:
            if actual_mode == "video":
                assert verified is not None
                response = await self._request(lambda: bot.send_video(
                    video=FSInputFile(path), width=verified.width, height=verified.height, supports_streaming=True, **kwargs), bot=bot)
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
        if captions:
            await self._remember_info(bot, response, captions)
        if actual_mode == 'video' and owner_id is not None:
            await self._attach_video_actions(bot, response, result.video_info, owner_id, preferences)
        media = getattr(response, actual_mode, None)
        file_id = getattr(media, "file_id", None)
        return SentMedia(actual_mode, file_id if isinstance(file_id, str) and file_id else None, size, verified)

    async def _send_preview(self, bot, chat_id, result, *, message_thread_id=None, owner_id=None, preferences=None):
        from bot.services.preview import create_preview
        with tempfile.TemporaryDirectory(prefix='.preview-', dir=result.file_path.parent) as temporary:
            path = Path(temporary) / 'preview.mp4'
            prepared = await create_preview(result.file_path, path, max_bytes=self.policy.max_upload_bytes,
                                            timeout=self.timeout, conversions=self.conversions,
                                            verified=result.verified_format)
            path, verified = prepared.path, prepared.format
            expected_duration = (result.verified_format.duration_seconds if result.verified_format else None) or result.video_info.duration_seconds
            if expected_duration and verified.duration_seconds and abs(expected_duration - verified.duration_seconds) > max(1, expected_duration * .05):
                raise CorruptFileError('Playable preview duration differs from the original.')
            if result.verified_format and result.verified_format.has_audio and not verified.has_audio:
                raise CorruptFileError('Playable preview lost the original audio.')
            preview = result.model_copy(update={'file_path': path, 'file_size_bytes': path.stat().st_size,
                                                'verified_format': verified})
            return await self.send_with_receipt(bot, chat_id, preview, 'video', message_thread_id=message_thread_id,
                                                owner_id=owner_id, preferences=preferences)

    async def _attach_video_actions(self, bot: Bot, message: Message, info: VideoInfo, owner_id: int, preferences=None):
        if self.pending is None or not isinstance(message, Message):
            return
        item = None
        try:
            item = self.pending.add(owner_id, message.chat.id, message.message_id, info,
                                    preferences=preferences, delivered=True)
            keyboard = delivered_video_keyboard(item.key, info, max_bytes=self.policy.max_upload_bytes, preferences=preferences)
            await self._request(lambda: bot.edit_message_reply_markup(chat_id=message.chat.id,
                message_id=message.message_id, reply_markup=keyboard), bot=bot, upload=False)
        except Exception as exc:
            if item is not None:
                self.pending.remove(item.key)
            logger.warning('Video delivered but action controls unavailable error=%s', type(exc).__name__)

    async def _send_audio(self, bot: Bot, chat_id: int, result: DownloadResult,
                          *, message_thread_id: int | None = None) -> SentMedia:
        verified = result.verified_format
        if (not result.audio_only or result.file_path.suffix.lower() != '.mp3' or verified is None
                or verified.has_audio is not True or verified.has_video is not False):
            raise CorruptFileError('Expected verified audio-only MP3.')
        kwargs = dict(chat_id=chat_id, audio=FSInputFile(result.file_path),
                      title=(result.video_info.title or 'Audio')[:64],
                      caption=audio_caption(result.video_info), parse_mode='HTML')
        captions = audio_captions(result.video_info, verified, result.file_size_bytes) if self.details is not None else None
        if captions:
            kwargs['caption'] = captions[0]
            kwargs['reply_markup'] = media_info_keyboard()
        if verified.duration_seconds:
            kwargs['duration'] = int(verified.duration_seconds)
        if result.video_info.uploader:
            kwargs['performer'] = result.video_info.uploader[:64]
        if message_thread_id is not None:
            kwargs['message_thread_id'] = message_thread_id
        try:
            response = await self._request(lambda: bot.send_audio(**kwargs), bot=bot)
        except TelegramEntityTooLarge as exc:
            raise FileSizeLimitError('Telegram rejected the audio size.') from exc
        except TelegramAPIError as exc:
            raise TelegramDeliveryError('Telegram audio delivery failed.') from exc
        if captions:
            await self._remember_info(bot, response, captions)
        file_id = getattr(getattr(response, 'audio', None), 'file_id', None)
        return SentMedia('audio', file_id if isinstance(file_id, str) else None)

    @scoped
    async def send_photos(self, bot: Bot, chat_id: int, result: DownloadResult, mode: SendMode,
                          *, message_thread_id: int | None = None) -> None:
        if mode not in {"photos", "album", "zip"} or not result.photos:
            raise TelegramDeliveryError("Invalid photo action or empty album.")
        kwargs = dict(chat_id=chat_id)
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        # Preflight the entire album before the first upload, preserving original bytes.
        for photo in result.photos:
            path = photo.file_path
            if path.is_symlink() or not path.is_file() or path.stat().st_size != photo.size:
                raise CorruptFileError("Original photo is missing or changed.")
            self.check_size(photo.size)
        try:
            if mode == "zip":
                path = result.file_path
                if path.is_symlink() or not path.is_file() or path.stat().st_size != result.file_size_bytes:
                    raise CorruptFileError("ZIP is missing or changed.")
                self.check_size(result.file_size_bytes)
                dimensions = list(dict.fromkeys(f"{p.width}×{p.height}" for p in result.photos))
                captions = zip_captions(result) if self.details is not None else None
                options = {"reply_markup": media_info_keyboard(photo=True), "parse_mode": "HTML"} if captions else {}
                response = await self._request(lambda: bot.send_document(document=FSInputFile(path),
                    caption=captions[0] if captions else "Fotografii originale, în ordinea postării. ZIP fără recodare.\nRezoluții verificate: "
                            + ", ".join(dimensions[:12]), **kwargs, **options), bot=bot)
                if captions:
                    await self._remember_info(bot, response, captions)
                return
            preview = mode == "album" and all(
                p.size <= 10_000_000 and p.width + p.height <= 10000
                and max(p.width, p.height) / min(p.width, p.height) <= 20
                and p.file_path.suffix.lower() in {".jpg", ".png"} for p in result.photos)
            for start in range(0, len(result.photos), 10):
                batch = result.photos[start:start + 10]
                caption = "Album foto (Telegram poate comprima). Pentru originale alege fișiere sau ZIP." if preview else "Fotografii originale."
                captions = [f"{start + i + 1}/{len(result.photos)} · Rezoluție verificată: {p.width}×{p.height}"
                            + ("\n" + caption if i == 0 else "") for i, p in enumerate(batch)]
                detail_captions = [photo_captions(result.video_info, p, start + i, preview=preview)
                                   for i, p in enumerate(batch)] if self.details is not None else []
                if detail_captions:
                    captions = [pair[0] for pair in detail_captions]
                if len(batch) == 1:
                    options = {"reply_markup": media_info_keyboard(photo=True), "parse_mode": "HTML"} if detail_captions else {}
                    path = FSInputFile(batch[0].file_path)
                    if preview:
                        response = await self._request(lambda: bot.send_photo(photo=path, caption=captions[0], **kwargs, **options), bot=bot)
                    else:
                        response = await self._request(lambda: bot.send_document(document=path, caption=captions[0],
                            disable_content_type_detection=True, **kwargs, **options), bot=bot)
                    if detail_captions:
                        await self._remember_info(bot, response, detail_captions[0])
                else:
                    media = [(InputMediaPhoto(media=FSInputFile(p.file_path), caption=captions[i], parse_mode="HTML" if detail_captions else None)
                              if preview else InputMediaDocument(media=FSInputFile(p.file_path),
                                  caption=captions[i], parse_mode="HTML" if detail_captions else None, disable_content_type_detection=True))
                             for i, p in enumerate(batch)]
                    responses = await self._request(lambda: bot.send_media_group(media=media, **kwargs), bot=bot)
                    if detail_captions:
                        for response, pair in zip(responses, detail_captions):
                            await self._remember_info(bot, response, pair, attach=True)
        except TelegramEntityTooLarge as exc:
            raise FileSizeLimitError("Telegram rejected the upload size.") from exc
        except TelegramAPIError as exc:
            raise TelegramDeliveryError("Photo delivery failed; earlier groups may already have arrived.") from exc

    async def _send_video_zip(self, bot: Bot, chat_id: int, result: DownloadResult,
                              *, message_thread_id: int | None = None) -> SentMedia:
        with tempfile.TemporaryDirectory(prefix=".tiktok-zip-", dir=result.file_path.parent) as temporary:
            archive = Path(temporary) / "original-video.zip"
            def build():
                with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
                    output.write(result.file_path, arcname="video" + result.file_path.suffix)
            with stage("conversion"):
                task = asyncio.create_task(asyncio.to_thread(build))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
            size = archive.stat().st_size
            self.check_size(size)
            captions = video_zip_captions(result.video_info, result.verified_format, result.format, size)
            kwargs = dict(chat_id=chat_id, caption=captions[0], parse_mode="HTML")
            if self.details is not None:
                kwargs["reply_markup"] = media_info_keyboard()
            if message_thread_id is not None:
                kwargs["message_thread_id"] = message_thread_id
            try:
                response = await self._request(lambda: bot.send_document(document=FSInputFile(archive), **kwargs), bot=bot)
            except TelegramEntityTooLarge as exc:
                raise FileSizeLimitError("Telegram rejected ZIP size.") from exc
            except TelegramAPIError as exc:
                raise TelegramDeliveryError("Telegram ZIP delivery failed.") from exc
            if self.details is not None:
                await self._remember_info(bot, response, captions)
            file_id = getattr(getattr(response, "document", None), "file_id", None)
            return SentMedia("zip", file_id if isinstance(file_id, str) else None, size)

    async def _remember_info(self, bot: Bot, message: Message, captions: tuple[str, str], *, attach: bool = False) -> None:
        if self.details is None or not isinstance(message, Message):
            return
        try:
            await self.details.put_media_details(bot.id, message.chat.id, message.message_id, *captions)
            if attach:
                await self._request(lambda: bot.edit_message_reply_markup(chat_id=message.chat.id,
                    message_id=message.message_id, reply_markup=media_info_keyboard(photo=True)), bot=bot, upload=False)
        except Exception as exc:
            # The media is already delivered. Never retry an upload for a metadata error.
            logger.warning("Media info unavailable after delivery error=%s", type(exc).__name__)

    async def _request(self, operation: Callable[[], Awaitable[T]], *, bot: Bot, upload: bool = True) -> T:
        with stage("upload") if upload else nullcontext():
            try:
                async with asyncio.timeout(self.timeout):
                    if getattr(getattr(bot, "session", None), "_retry_managed", False) is True:
                        return await operation()
                    return await retry_async(operation, retry_network=False)
            except TimeoutError as exc:
                raise TelegramDeliveryError("Telegram send timed out; delivery status is uncertain.") from exc

    @scoped
    async def send_cached(self, bot: Bot, chat_id: int, entry: CacheEntry, info: VideoInfo,
                          *, message_thread_id: int | None = None, owner_id: int | None = None, preferences=None) -> None:
        self.check_size(entry.size)
        if entry.kind in {"document", "zip"} and not entry.original:
            raise InvalidCachedFileError("Cached media is not an original document.")
        captions = video_captions(info, entry.verified, entry.source, entry.size, document=entry.kind == "document") if self.details is not None else None
        if entry.kind == "zip":
            captions = video_zip_captions(info, entry.verified, entry.source, entry.size)
        if entry.kind == "audio":
            captions = audio_captions(info, entry.verified, entry.size) if self.details is not None else None
        kwargs = dict(chat_id=chat_id, caption=captions[0] if captions else video_details(info, entry.verified, entry.source, entry.size), parse_mode="HTML")
        if entry.kind == "audio":
            kwargs['caption'] = audio_caption(info)
        if captions:
            kwargs["reply_markup"] = media_info_keyboard()
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        try:
            if entry.kind == "audio":
                response = await self._request(lambda: bot.send_audio(audio=entry.file_id, **kwargs), bot=bot)
            elif entry.kind == "video":
                response = await self._request(lambda: bot.send_video(video=entry.file_id, **kwargs), bot=bot)
            else:
                response = await self._request(lambda: bot.send_document(document=entry.file_id, **kwargs), bot=bot)
            if captions:
                await self._remember_info(bot, response, captions)
            if entry.kind == 'video' and owner_id is not None:
                await self._attach_video_actions(bot, response, info, owner_id, preferences)
        except TelegramBadRequest as exc:
            if any(text in exc.message.lower() for text in ("file_id", "file identifier", "file reference", "wrong remote file")):
                raise InvalidCachedFileError("Telegram rejected a cached file identifier.") from exc
            raise TelegramDeliveryError("Telegram rejected the cached media send.") from exc
        except TelegramAPIError as exc:
            logger.warning("Telegram cached-send error=%s", type(exc).__name__)
            raise TelegramDeliveryError("Telegram cached media delivery failed.") from exc


def audio_caption(info: VideoInfo) -> str:
    return media_title(info)
