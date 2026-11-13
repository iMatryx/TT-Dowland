"""Stateless /start and /help commands."""

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, MenuButtonCommands, Message

from bot.config import Settings
from bot.handlers.keyboards import main_menu
from bot.services.telegram_sender import TelegramSender
from bot.utils.telegram_text import START_TEXT


async def register_commands(bot: Bot) -> None:
    """Publish commands for Telegram's slash suggestions and menu button."""
    await bot.set_my_commands([
        BotCommand(command="start", description="Pornește botul și deschide meniul"),
        BotCommand(command="help", description="Cum folosești botul"),
        BotCommand(command="menu", description="Deschide meniul principal"),
        BotCommand(command="settings", description="Preferințe de descărcare"),
        BotCommand(command="cancel", description="Anulează cererile în așteptare"),
    ])
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def start_handler(message: Message) -> None:
    await message.answer(START_TEXT, parse_mode="HTML", reply_markup=main_menu())


async def help_handler(message: Message, settings: Settings, sender: TelegramSender) -> None:
    limit = min(settings.max_file_size_mb * 1024 * 1024, sender.policy.max_upload_bytes)
    await message.answer(
        "<b>Cum folosești botul</b>\n\n"
        "1. Trimite un link public TikTok, YouTube Shorts sau Instagram Reels. "
        "Accept și linkuri scurte TikTok și youtu.be.\n"
        "2. Videoclipul se trimite automat, redabil în chat. Sub el ai "
        "<b>📁 Ca fișier</b> pentru original și <b>ℹ️ Info</b> pentru detalii. "
        "Dacă originalul nu este compatibil cu playerul Telegram, creez un MP4 pentru redare; "
        "varianta ca fișier păstrează originalul, fără conversie.\n\n"
        "🎵 <b>Doar audio</b>: extrag sunetul videoclipului și îl trimit ca MP3. "
        "MP3: 192 kbps. Conversia nu îmbunătățește sursa.\n\n"
        "3. Pentru fotografii TikTok: toate originalele ca fișiere, album foto sau ZIP original. "
        "Telegram poate comprima albumul foto. Grupurile au maximum 10 elemente; "
        "imaginile incompatibile sunt trimise ca documente.\n\n"
        "💎 Aleg cea mai bună variantă disponibilă la sursă, fără upscaling. "
        "Nu toate videoclipurile există în 4K. Selectez numai variante identificate fără watermark; "
        "informația este declarată de sursă și nu verificată vizual.\n\n"
        f"📦 Limita efectivă curentă: <b>{limit / 1_000_000:g} MB</b>. "
        "Telegram Premium al utilizatorului nu crește limita de upload a botului.\n\n"
        "🔒 Videoclipurile private, șterse sau restricționate pot fi indisponibile. "
        "Vei primi o explicație; nu încerc ocolirea restricțiilor.\n\n"
        "Butoanele expiră după 10 minute și după repornire. "
        "Poți trimite oricând alt link, fără să repeți /start.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


def create_router() -> Router:
    router = Router(name="commands")
    router.message.register(start_handler, CommandStart())
    router.message.register(start_handler, F.text.strip().lower() == "start")
    router.message.register(help_handler, Command("help"))
    return router
