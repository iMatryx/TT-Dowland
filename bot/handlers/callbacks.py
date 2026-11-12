"""Download callbacks with ownership checks, duplicate protection, and cleanup."""

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InaccessibleMessage

from bot.handlers.keyboards import DownloadCallback, download_keyboard
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
        await query.answer("Buton invalid. Trimite din nou linkul.", show_alert=True)
        return
    item = pending.get(action.key)
    message = query.message
    if (item is None or message is None or isinstance(message, InaccessibleMessage)
            or item.user_id != query.from_user.id or item.chat_id != message.chat.id
            or item.message_id != message.message_id):
        await query.answer("Buton expirat sau destinat altui utilizator. Trimite din nou linkul.", show_alert=True)
        return
    if item.busy:
        await query.answer("Descărcarea este deja în curs.", show_alert=True)
        return
    item.busy = True
    progress = ProgressMessage(message)
    result = None
    try:
        await query.answer()
        if runtime is not None:
            await progress.update("⏳ Cererea este în coadă...")
            await runtime.deliver(bot, item.chat_id, item.info, action.mode, download_service,
                                  sender, progress, message_thread_id=message.message_thread_id)
        else:
            await progress.update("📥 Descarc videoclipul...")
            result = await download_service.download(str(item.info.webpage_url))
            await progress.update("📤 Pregătesc trimiterea...")
            await sender.send(bot, item.chat_id, result, action.mode,
                              message_thread_id=message.message_thread_id)
    except Exception as exc:
        logger.warning("TikTok download or delivery failed (%s)", type(exc).__name__)
        try:
            await progress.update(error_text(exc), reply_markup=download_keyboard(item.key))
            item.message_id = progress.message.message_id
        except TelegramAPIError:
            logger.warning("Could not update Telegram failure status")
    else:
        pending.remove(item.key)
        try:
            await progress.update("✅ Videoclipul este gata!")
        except TelegramAPIError:
            logger.warning("Media sent but completion status could not be edited")
    finally:
        item.busy = False
        if result is not None:
            try:
                result.file_path.unlink(missing_ok=True)
            except OSError:
                logger.error("Could not remove a completed Telegram download")


def create_router() -> Router:
    router = Router(name="download_callbacks")
    router.callback_query.register(callback_handler, F.data.startswith("dl:"))
    return router
