"""Stateless /start and /help commands."""

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeChatMember, Chat, MenuButtonCommands, Message

from bot.config import Settings
from bot.handlers.keyboards import main_menu
from bot.services.telegram_sender import TelegramSender
from bot.utils.telegram_text import START_TEXT
from bot.services.cache import SQLiteCache
from bot.utils.i18n import LANGUAGES, language_context, tr


def interface_commands(*, include_restart: bool = False) -> list[BotCommand]:
    commands = [
        BotCommand(command="start", description=tr("Pornește botul și alege limba")),
        BotCommand(command="language", description=tr("Schimbă limba")),
        BotCommand(command="help", description=tr("Cum folosești botul")),
        BotCommand(command="menu", description=tr("Deschide meniul principal")),
        BotCommand(command="cancel", description=tr("Anulează cererile în așteptare")),
    ]
    if include_restart:
        commands.append(BotCommand(command='restart', description=tr('Repornește botul (administrator)')))
    return commands


async def register_commands(bot: Bot) -> None:
    """Register platform-language fallbacks and replace any obsolete defaults."""
    for language in LANGUAGES:
        with language_context(language):
            await bot.set_my_commands(interface_commands(), language_code=language)
    with language_context('ro'):
        await bot.set_my_commands(interface_commands())
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def register_selected_commands(bot: Bot, chat: Chat, user_id: int, settings: Settings | None = None) -> None:
    # A group member's choice must not change other members' command menus.
    if chat.type == 'private':
        scope = BotCommandScopeChat(chat_id=chat.id)
    elif chat.type in {'group', 'supergroup'}:
        scope = BotCommandScopeChatMember(chat_id=chat.id, user_id=user_id)
    else:
        return
    admin = bool(settings is not None and settings.restart_enabled and user_id in settings.administrator_ids and chat.type == 'private')
    await bot.set_my_commands(interface_commands(include_restart=admin), scope=scope)


async def start_handler(message: Message, language_store: SQLiteCache) -> None:
    from bot.handlers.language import show_language_choice
    await show_language_choice(message, language_store)


async def welcome_handler(message: Message) -> None:
    await message.answer(tr(START_TEXT), parse_mode="HTML", reply_markup=main_menu())


async def help_handler(message: Message, settings: Settings, sender: TelegramSender) -> None:
    limit = min(settings.max_file_size_mb * 1024 * 1024, sender.policy.max_upload_bytes)
    await message.answer(
        tr("<b>Cum folosești botul</b>\n\n"
           "1. Trimite un link public TikTok, YouTube Shorts sau Instagram Reels.\n"
           "2. Videoclipul apare automat în chat.\n"
           "3. Alege 📁 Ca fișier pentru original, 🎵 Doar audio pentru sunet sau ℹ️ Info pentru detalii.\n\n"
           "📦 Limită per fișier: {limit:g} MB.", limit=limit / 1_000_000),
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


def create_router() -> Router:
    router = Router(name="commands")
    router.message.register(start_handler, CommandStart())
    router.message.register(start_handler, F.text.strip().lower() == "start")
    router.message.register(start_handler, Command("language"))
    router.message.register(help_handler, Command("help"))
    return router
