"""Download callbacks with ownership checks, duplicate protection, and cleanup."""

import logging

from bot.utils.i18n import tr

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from bot.handlers.keyboards import CancelCallback, DownloadCallback, VideoActionCallback, download_keyboard, available_actions, main_menu
from bot.services.telegram_download import PendingRequests, TelegramDownloadService
from bot.services.telegram_sender import TelegramSender
from bot.utils.telegram_progress import ProgressMessage
from bot.utils.telegram_text import error_text

logger = logging.getLogger(__name__)


async def callback_handler(query: CallbackQuery, bot: Bot, download_service: TelegramDownloadService,
                           pending: PendingRequests, sender: TelegramSender, runtime=None) -> None:
    try:
        action = DownloadCallback.unpack(query.data or "")
    except (ValueError, TypeError):
        await query.answer(tr('Buton invalid. Trimite din nou linkul.'), show_alert=True)
        return
    item = pending.get(action.key)
    message = query.message
    if (item is None or message is None or isinstance(message, InaccessibleMessage)
            or item.user_id != query.from_user.id or item.chat_id != message.chat.id
            or item.message_id != message.message_id):
        await query.answer(tr('Buton expirat sau destinat altui utilizator. Trimite din nou linkul.'), show_alert=True)
        return
    if item.busy:
        await query.answer(tr('Descărcarea este deja în curs.'), show_alert=True)
        return
    limit = min(sender.policy.max_upload_bytes, getattr(download_service, 'max_download_bytes', sender.policy.max_upload_bytes))
    allowed = {mode for mode, _ in available_actions(item.info, max_bytes=limit)}
    legacy = {'video', 'document'} if item.info.media_type == 'video' else {'photos', 'album', 'zip'}
    if action.mode not in allowed and not (action.mode in legacy):
        await query.answer(tr('Buton vechi sau opțiune indisponibilă. Trimite din nou linkul.'), show_alert=True)
        return
    item.busy = True
    try:
        await query.answer()
        mode = 'preview' if action.mode == 'chat' and item.info.media_type == 'video' else action.mode
        await deliver_pending(bot, item, mode, download_service, pending, sender,
                              ProgressMessage(message), runtime=runtime, owner_id=query.from_user.id)
    finally:
        item.busy = False


async def deliver_pending(bot, item, mode, download_service, pending, sender, progress, *, runtime=None, owner_id=None):
    """Deliver one owner-approved request; share cleanup with automatic defaults."""
    result = None
    try:
        if runtime is not None:
            await progress.update(tr('⏳ Cererea este în coadă...'))
            await runtime.deliver(bot, item.chat_id, item.info, mode, download_service,
                                  sender, progress, message_thread_id=progress.message.message_thread_id,
                                  **({'owner_id': owner_id} if owner_id is not None else {}))
        else:
            await progress.update(tr('📥 Descarc postarea...'))
            audio_options = {'audio_only': True} if mode == 'audio' else {}
            result = await download_service.download(str(item.info.webpage_url), **audio_options)
            await progress.update(tr('📤 Pregătesc trimiterea...'))
            await sender.send(bot, item.chat_id, result, mode,
                              message_thread_id=progress.message.message_thread_id,
                              **({'owner_id': owner_id} if owner_id is not None else {}))
    except Exception as exc:
        logger.warning("TikTok download or delivery failed (%s)", type(exc).__name__)
        try:
            await progress.update(error_text(exc), reply_markup=download_keyboard(item.key, item.info))
            item.message_id = progress.message.message_id
        except TelegramAPIError:
            logger.warning("Could not update Telegram failure status")
    else:
        pending.remove(item.key)
        try:
            await progress.update(tr('✅ Postarea a fost trimisă!'))
        except TelegramAPIError:
            logger.warning("Media sent but completion status could not be edited")
    finally:
        item.busy = False
        if result is not None:
            try:
                result.cleanup()
            except OSError:
                logger.error("Could not remove a completed Telegram download")


def create_router() -> Router:
    router = Router(name="download_callbacks")
    router.callback_query.register(callback_handler, F.data.startswith("dl:"))
    router.callback_query.register(video_action_handler, F.data.startswith('va:'))
    router.callback_query.register(cancel_handler, F.data.startswith("cancel:"))
    router.message.register(cancel_command, Command("cancel"))
    return router


async def video_action_handler(query: CallbackQuery, bot: Bot, download_service: TelegramDownloadService,
                               pending: PendingRequests, sender: TelegramSender, runtime=None) -> None:
    try:
        action = VideoActionCallback.unpack(query.data or '')
    except (ValueError, TypeError):
        await query.answer(tr('Buton invalid.'), show_alert=True)
        return
    item = pending.get(action.key)
    message = query.message
    if (item is None or message is None or isinstance(message, InaccessibleMessage)
            or item.user_id != query.from_user.id or item.chat_id != message.chat.id
            or item.message_id != message.message_id):
        await query.answer(tr('Buton expirat sau destinat altui utilizator. Trimite din nou linkul.'), show_alert=True)
        return
    if item.busy:
        await query.answer(tr('Cererea este deja în curs.'), show_alert=True)
        return
    limit = min(sender.policy.max_upload_bytes, getattr(download_service, 'max_download_bytes', sender.policy.max_upload_bytes))
    if action.mode not in dict(available_actions(item.info, max_bytes=limit)):
        await query.answer(tr('Opțiune indisponibilă. Trimite din nou linkul.'), show_alert=True)
        return
    item.busy = True
    try:
        await query.answer()
        progress = ProgressMessage(await message.answer(tr('⏳ Pregătesc cererea...'), parse_mode='HTML'))
        operation = pending.add(item.user_id, item.chat_id, progress.message.message_id, item.info)
        operation.busy = True
        await deliver_pending(bot, operation, action.mode, download_service, pending, sender, progress, runtime=runtime)
    finally:
        item.busy = False


async def cancel_handler(query: CallbackQuery, pending: PendingRequests) -> None:
    try:
        action = CancelCallback.unpack(query.data or "")
    except (ValueError, TypeError):
        await query.answer(tr('Buton invalid.'), show_alert=True)
        return
    item = pending.get(action.key)
    message = query.message
    if (item is None or message is None or isinstance(message, InaccessibleMessage)
            or item.user_id != query.from_user.id or item.chat_id != message.chat.id
            or item.message_id != message.message_id):
        await query.answer(tr('Buton expirat sau destinat altui utilizator.'), show_alert=True)
        return
    if item.busy:
        await query.answer(tr('Trimiterea a început deja. Acest buton anulează doar alegerea în așteptare.'), show_alert=True)
        return
    pending.remove(item.key)
    await query.answer(tr('Alegere anulată.'))
    await ProgressMessage(message).update(tr('❌ Alegere anulată. Poți trimite alt link TikTok, Shorts sau Reels.'))


async def cancel_command(message: Message, bot: Bot, pending: PendingRequests) -> None:
    if message.from_user is None:
        return
    items = pending.cancel_for(message.from_user.id, message.chat.id)
    for item in items:
        try:
            await bot.edit_message_reply_markup(chat_id=item.chat_id, message_id=item.message_id, reply_markup=None)
        except TelegramAPIError:
            logger.warning("Could not remove canceled choice buttons")
    text = tr('❌ Alegerile în așteptare au fost anulate.') if items else tr('Nu ai alegeri în așteptare. Trimiterile începute nu sunt întrerupte.')
    await message.answer(text, reply_markup=main_menu())
