"""Stateless /start and /help commands."""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.config import Settings
from bot.services.telegram_sender import TelegramSender
from bot.utils.telegram_text import START_TEXT


async def start_handler(message: Message) -> None:
    await message.answer(START_TEXT, parse_mode="HTML")


async def help_handler(message: Message, settings: Settings, sender: TelegramSender) -> None:
    limit = min(settings.max_file_size_mb * 1024 * 1024, sender.policy.max_upload_bytes)
    await message.answer(
        "<b>Cum folosești botul</b>\n\n"
        "1. Trimite un link TikTok public, complet sau scurt (vm/vt.tiktok.com).\n"
        "2. După analiză, alege <b>📹 Descarcă video</b> pentru redare în Telegram "
        "sau <b>📁 Descarcă original</b> pentru fișierul original ca document. "
        "Dacă formatul nu este compatibil ca video, îl trimit ca document, fără conversie.\n\n"
        "💎 Aleg cea mai bună variantă disponibilă la sursă, fără upscaling. "
        "Nu toate videoclipurile există în 4K. Absența watermark-ului nu este garantată; "
        "informația poate fi necunoscută sau doar declarată de sursă.\n\n"
        f"📦 Limita efectivă curentă: <b>{limit / 1_000_000:g} MB</b>. "
        "Telegram Premium al utilizatorului nu crește limita de upload a botului.\n\n"
        "🔒 Videoclipurile private, șterse sau restricționate pot fi indisponibile. "
        "Vei primi o explicație; nu încerc ocolirea restricțiilor.\n\n"
        "Butoanele expiră după 10 minute și după repornire. "
        "Poți trimite oricând alt link, fără să repeți /start.",
        parse_mode="HTML",
    )


def create_router() -> Router:
    router = Router(name="commands")
    router.message.register(start_handler, CommandStart())
    router.message.register(help_handler, Command("help"))
    return router
