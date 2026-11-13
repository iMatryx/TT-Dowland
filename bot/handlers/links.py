"""Detect a single TikTok link, analyze it, and offer owner-bound actions."""

import logging

from bot.utils.i18n import tr
import re

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.models.exceptions import InvalidURLError, FileSizeLimitError
from bot.services.media_info import media_title
from bot.services.telegram_download import PendingRequests, TelegramDownloadService
from bot.services.telegram_sender import TelegramSender
from bot.services.urls import normalize_url
from bot.utils.telegram_progress import ProgressMessage
from bot.utils.telegram_text import error_text
from bot.handlers.keyboards import available_actions, download_keyboard

logger = logging.getLogger(__name__)
LINK_PATTERN = re.compile(
    r"(?<![\w./@?=&-])(?:https?://|(?:www\.|vm\.|vt\.|m\.)?(?:tiktok\.com|youtube\.com|youtu\.be|instagram\.com)/)[^\s<>]+", re.I,
)


def message_url(message: Message) -> str:
    candidates = LINK_PATTERN.findall(message.text or "")
    candidates.extend(entity.url for entity in (message.entities or []) if entity.type == "text_link" and entity.url)
    valid = set()
    for candidate in candidates:
        try:
            valid.add(normalize_url(candidate.rstrip(".,!?;)]}\"'")))
        except InvalidURLError:
            continue
    if len(valid) != 1:
        raise InvalidURLError("Expected one valid TikTok video link.")
    return valid.pop()


async def link_handler(message: Message, download_service: TelegramDownloadService,
                       pending: PendingRequests, sender: TelegramSender, runtime=None,
                       bot: Bot | None = None) -> None:
    try:
        url = message_url(message)
    except InvalidURLError as exc:
        await message.answer(error_text(exc), parse_mode="HTML")
        return
    if message.from_user is None:
        await message.answer(tr('Trimite linkul dintr-un cont personal pentru a putea folosi butoanele.'))
        return
    progress = ProgressMessage(await message.answer(tr('⏳ Analizez postarea...'), parse_mode="HTML"))
    item = None
    try:
        info = (await runtime.analyze(download_service, url) if runtime is not None
                else await download_service.analyze(url))
        limit = min(sender.policy.max_upload_bytes, getattr(download_service, "max_download_bytes", sender.policy.max_upload_bytes))
        keyboard = download_keyboard("placeholder", info, max_bytes=limit)
        if not keyboard.inline_keyboard:
            raise FileSizeLimitError("Selected source variant exceeds the limit; no quality reduction is attempted.")
        item = pending.add(message.from_user.id, message.chat.id, progress.message.message_id, info)
        await progress.update(
            media_title(info),
            reply_markup=download_keyboard(item.key, info, max_bytes=limit),
        )
        item.message_id = progress.message.message_id
        if bot is not None and info.media_type == 'video' and 'chat' in dict(available_actions(
                info, max_bytes=limit)):
            from bot.handlers.callbacks import deliver_pending
            item.busy = True
            await deliver_pending(bot, item, 'preview', download_service, pending, sender, progress,
                                  runtime=runtime, owner_id=message.from_user.id)
    except Exception as exc:
        if item is not None:
            pending.remove(item.key)
        logger.warning("TikTok analysis failed (%s)", type(exc).__name__)
        await progress.update(error_text(exc))


def create_router() -> Router:
    router = Router(name="tiktok_links")
    router.message.register(link_handler, F.text & ~F.text.startswith("/"))
    return router
