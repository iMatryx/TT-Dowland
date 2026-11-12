"""Detect a single TikTok link, analyze it, and offer owner-bound actions."""

import logging
import re

from aiogram import F, Router
from aiogram.types import Message

from bot.models.exceptions import InvalidURLError
from bot.services.quality import select_formats
from bot.services.telegram_download import PendingRequests, TelegramDownloadService
from bot.services.telegram_sender import TelegramSender
from bot.services.urls import normalize_url
from bot.utils.telegram_progress import ProgressMessage
from bot.utils.telegram_text import error_text, video_details
from bot.handlers.keyboards import download_keyboard

logger = logging.getLogger(__name__)
LINK_PATTERN = re.compile(
    r"(?<![\w./@?=&-])(?:https?://|(?:www\.|vm\.|vt\.)?tiktok\.com/)[^\s<>]+", re.I,
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
                       pending: PendingRequests, sender: TelegramSender, runtime=None) -> None:
    try:
        url = message_url(message)
    except InvalidURLError as exc:
        await message.answer(error_text(exc), parse_mode="HTML")
        return
    if message.from_user is None:
        await message.answer("Trimite linkul dintr-un cont personal pentru a putea folosi butoanele.")
        return
    progress = ProgressMessage(await message.answer("⏳ Analizez videoclipul...", parse_mode="HTML"))
    item = None
    try:
        info = (await runtime.analyze(download_service, url) if runtime is not None
                else await download_service.analyze(url))
        selection = select_formats(info.formats)
        sender.check_size(selection.known_size_bytes)
        item = pending.add(message.from_user.id, message.chat.id, progress.message.message_id, info)
        await progress.update(
            video_details(info, None, selection.video) + "\n\nAlege cum dorești să primești fișierul:",
            reply_markup=download_keyboard(item.key),
        )
        item.message_id = progress.message.message_id
    except Exception as exc:
        if item is not None:
            pending.remove(item.key)
        logger.warning("TikTok analysis failed (%s)", type(exc).__name__)
        await progress.update(error_text(exc))


def create_router() -> Router:
    router = Router(name="tiktok_links")
    router.message.register(link_handler, F.text & ~F.text.startswith("/"))
    return router
