"""Persistent download, help and limits menu."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import Settings
from bot.utils.i18n import tr, translations
from bot.handlers.commands import help_handler, welcome_handler
from bot.handlers.keyboards import (
    MENU_DOWNLOAD, MENU_HELP, MENU_LIMITS,
    main_menu,
)
from bot.services.telegram_sender import TelegramSender

async def download_prompt(message: Message) -> None:
    await message.answer(tr('📥 Trimite un link public TikTok (video sau fotografii), YouTube Shorts ori Instagram Reels.'), reply_markup=main_menu())


async def show_limits(message: Message, settings: Settings, sender: TelegramSender) -> None:
    limit = min(settings.max_file_size_mb * 1024 * 1024, sender.policy.max_upload_bytes)
    await message.answer(
        tr('ℹ️ <b>Limite și calitate</b>\n\n'
           '📦 Maximum {limit:g} MB per fișier.\n'
           '⏱ Maximum {requests} cereri pe minut.\n'
           '💎 Cea mai bună calitate disponibilă la sursă.\n'
           '💧 Doar variante identificate fără watermark.\n'
           '🔒 Doar postări publice.', limit=limit / 1_000_000, requests=settings.user_requests_per_minute),
        parse_mode='HTML', reply_markup=main_menu())


def create_router() -> Router:
    router = Router(name='persistent_menu')
    router.message.register(welcome_handler, Command('menu'))
    router.message.register(download_prompt, F.text.in_(translations(MENU_DOWNLOAD)))
    router.message.register(download_prompt, F.text == '📥 Descarcă TikTok')
    router.message.register(help_handler, F.text.in_(translations(MENU_HELP)))
    router.message.register(show_limits, F.text.in_(translations(MENU_LIMITS)))
    return router
