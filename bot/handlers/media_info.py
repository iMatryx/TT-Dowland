"""Show/hide saved media metadata by editing its caption in place."""

import logging
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, InaccessibleMessage, InlineKeyboardMarkup

from bot.handlers.keyboards import PendingInfoCallback, download_keyboard, media_info_keyboard
from bot.services.cache import SQLiteCache
from bot.services.media_info import selection_captions
from bot.services.telegram_download import PendingRequests, TelegramDownloadService
from bot.services.telegram_sender import TelegramSender
from bot.utils.telegram_progress import ProgressMessage

logger = logging.getLogger(__name__)


async def pending_info_handler(query: CallbackQuery, pending: PendingRequests,
                               sender: TelegramSender, download_service: TelegramDownloadService) -> None:
    try:
        action = PendingInfoCallback.unpack(query.data or '')
    except (ValueError, TypeError):
        await query.answer('Buton invalid.', show_alert=True)
        return
    item = pending.get(action.key)
    message = query.message
    if (item is None or message is None or isinstance(message, InaccessibleMessage)
            or item.user_id != query.from_user.id or item.chat_id != message.chat.id
            or item.message_id != message.message_id):
        await query.answer('Buton expirat sau destinat altui utilizator. Trimite din nou linkul.', show_alert=True)
        return
    if item.busy:
        await query.answer('Descărcarea este în curs.', show_alert=True)
        return
    item.busy = True
    try:
        captions = selection_captions(item.info, item.preferences)
        expanded = action.action == 'show'
        limit = min(sender.policy.max_upload_bytes, getattr(download_service, 'max_download_bytes', sender.policy.max_upload_bytes))
        progress = ProgressMessage(message)
        await progress.update(captions[1 if expanded else 0], reply_markup=download_keyboard(
            item.key, item.info, max_bytes=limit, preferences=item.preferences, expanded=expanded))
        item.message_id = progress.message.message_id
    except TelegramAPIError:
        await query.answer('Nu am putut actualiza informațiile. Încearcă din nou.', show_alert=True)
        return
    finally:
        item.busy = False
    await query.answer()


async def media_info_handler(query: CallbackQuery, bot: Bot, preferences: SQLiteCache) -> None:
    message = query.message
    if query.data not in {'mi:show', 'mi:hide'} or message is None or isinstance(message, InaccessibleMessage):
        await query.answer('Informațiile nu sunt disponibile.', show_alert=True)
        return
    try:
        saved = await preferences.get_media_details(bot.id, message.chat.id, message.message_id)
    except Exception as exc:
        logger.warning('Media information lookup failed error=%s', type(exc).__name__)
        await query.answer('Informațiile sunt temporar indisponibile.', show_alert=True)
        return
    if saved is None:
        await query.answer('Informațiile au expirat. Trimite din nou linkul pentru detalii.', show_alert=True)
        return
    expanded = query.data == 'mi:show'
    keyboard = media_info_keyboard(expanded=expanded, photo=saved[0].startswith(('🖼', '🗜')))
    existing = getattr(message, 'reply_markup', None)
    if isinstance(existing, InlineKeyboardMarkup):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            keyboard.inline_keyboard[0][0] if button.callback_data in {'mi:show', 'mi:hide'} else button
            for button in row] for row in existing.inline_keyboard])
    try:
        await message.edit_caption(caption=saved[1 if expanded else 0], parse_mode='HTML',
            reply_markup=keyboard)
    except TelegramBadRequest as exc:
        if 'message is not modified' not in exc.message.lower():
            await query.answer('Nu pot edita acest mesaj. Fișierul nu a fost retrimis.', show_alert=True)
            return
    except TelegramAPIError:
        await query.answer('Nu am putut actualiza informațiile. Încearcă din nou.', show_alert=True)
        return
    await query.answer()


def create_router() -> Router:
    router = Router(name='media_information')
    router.callback_query.register(media_info_handler, F.data.startswith('mi:'))
    router.callback_query.register(pending_info_handler, F.data.startswith('pi:'))
    return router
