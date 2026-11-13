"""Owner-bound language onboarding; /start always asks again."""
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InaccessibleMessage, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.services.cache import SQLiteCache
from bot.config import Settings
from bot.utils.i18n import LANGUAGES, language_context

logger = logging.getLogger(__name__)
LANGUAGE_PROMPT = '🌐 Alege limba / Выберите язык / Choose your language'
INVALID_CHOICE = 'Folosește /start. / Используйте /start. / Use /start.'


class LanguageCallback(CallbackData, prefix='language'):
    code: str
    token: str


def language_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=LanguageCallback(code=code, token=token).pack())
    ] for code, label in [('ro', '🇷🇴 Română'), ('ru', '🇷🇺 Русский'), ('en', '🇬🇧 English')]])


async def show_language_choice(message: Message, language_store: SQLiteCache) -> None:
    if message.from_user is None:
        return
    token = await language_store.begin_language_choice(message.from_user.id, message.chat.id)
    prompt = await message.answer(LANGUAGE_PROMPT, reply_markup=language_keyboard(token))
    await language_store.bind_language_choice(message.from_user.id, token, prompt.message_id)


async def select_language(query: CallbackQuery, language_store: SQLiteCache, settings: Settings) -> None:
    message = query.message
    try:
        choice = LanguageCallback.unpack(query.data or '')
    except (ValueError, TypeError):
        await query.answer(INVALID_CHOICE, show_alert=True)
        return
    if (choice.code not in LANGUAGES or message is None or isinstance(message, InaccessibleMessage)
            or not await language_store.select_language(query.from_user.id, message.chat.id,
                message.message_id, choice.token, choice.code)):
        await query.answer(INVALID_CHOICE, show_alert=True)
        return
    await query.answer()
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError as exc:
        logger.warning('Language selector markup unavailable error=%s', type(exc).__name__)
    from bot.handlers.commands import welcome_handler, register_selected_commands
    with language_context(choice.code):
        try:
            await register_selected_commands(query.bot, message.chat, query.from_user.id, settings)
        except TelegramAPIError as exc:
            logger.warning('Language command menu unavailable error=%s', type(exc).__name__)
        await welcome_handler(message)


def create_router() -> Router:
    router = Router(name='language_selection')
    router.callback_query.register(select_language, F.data.startswith('language:'))
    return router
