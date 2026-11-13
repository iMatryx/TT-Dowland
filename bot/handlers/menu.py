"""Persistent Telegram menu and owner-bound, saved delivery preferences."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from bot.config import Settings
from bot.handlers.commands import help_handler, start_handler
from bot.handlers.keyboards import (
    MENU_DOWNLOAD, MENU_HELP, MENU_LIMITS, MENU_PREFERENCES,
    PreferenceCallback, main_menu, preferences_keyboard,
)
from bot.models import VideoInfo
from bot.models.preferences import UserPreferences
from bot.services.cache import SQLiteCache
from bot.services.telegram_sender import TelegramSender

PREFERENCES_TEXT = (
    '💎 <b>Calitate maximă, fără watermark</b>\n\n'
    'Aleg mereu cea mai înaltă calitate disponibilă dintre variantele identificate '
    'de sursă fără watermark. Dacă nu există o asemenea variantă, nu trimit un video '
    'cu watermark sau cu statut necunoscut. Absența marcajului nu este verificată vizual.\n\n'
    'Originalele ca fișier rămân nemodificate. Pentru redarea în Telegram pot fi '
    'remuxate sau convertite în MP4. Audio se extrage la calitatea maximă disponibilă '
    'și se livrează ca MP3 de 192 kbps; conversia nu îmbunătățește sursa.'
)


async def download_prompt(message: Message) -> None:
    await message.answer('📥 Trimite un link public TikTok (video sau fotografii), YouTube Shorts ori Instagram Reels.', reply_markup=main_menu())


async def show_preferences(message: Message, preferences: SQLiteCache) -> None:
    if message.from_user is None:
        return
    saved = await preferences.get_preferences(message.from_user.id)
    await message.answer(PREFERENCES_TEXT, parse_mode='HTML',
                         reply_markup=preferences_keyboard(message.from_user.id, saved))


async def change_preference(query: CallbackQuery, preferences: SQLiteCache) -> None:
    try:
        action = PreferenceCallback.unpack(query.data or '')
    except (ValueError, TypeError):
        await query.answer('Opțiune invalidă.', show_alert=True)
        return
    if action.owner != query.from_user.id:
        await query.answer('Aceste preferințe aparțin altui utilizator. Deschide propriul meniu.', show_alert=True)
        return
    if query.message is None or isinstance(query.message, InaccessibleMessage):
        await query.answer('Deschide din nou Preferințe.', show_alert=True)
        return
    await query.answer('Calitatea maximă și lipsa watermark-ului sunt fixe. Deschide din nou meniul.', show_alert=True)


async def show_limits(message: Message, settings: Settings, sender: TelegramSender) -> None:
    limit = min(settings.max_file_size_mb * 1024 * 1024, sender.policy.max_upload_bytes)
    await message.answer(
        'ℹ️ <b>Limite și calitate</b>\n\n'
        f'📦 Limită efectivă per fișier trimis: <b>{limit / 1_000_000:g} MB</b>. '
        f'Pentru fotografii, limita locală totală este {settings.max_file_size_mb} MiB.\n'
        '💎 Fără upscaling sau comprimare automată a originalelor. '
        'Rezoluția se verifică după descărcare.\n'
        '💧 Watermarkul este evaluat din datele sursei; absența lui nu este garantată vizual.\n'
        '🖼 Albumele au maximum 10 elemente pe grup și pot fi comprimate de Telegram. '
        'Fișierele originale și ZIP păstrează octeții descărcați.\n'
        '🔒 Doar postări publice accesibile; restricțiile platformei pot împiedica extracția.',
        parse_mode='HTML', reply_markup=main_menu())


def create_router() -> Router:
    router = Router(name='persistent_menu')
    router.message.register(start_handler, Command('menu'))
    router.message.register(download_prompt, F.text == MENU_DOWNLOAD)
    router.message.register(download_prompt, F.text == '📥 Descarcă TikTok')
    router.message.register(show_preferences, Command('settings'))
    router.message.register(show_preferences, F.text == MENU_PREFERENCES)
    router.message.register(help_handler, F.text == MENU_HELP)
    router.message.register(show_limits, F.text == MENU_LIMITS)
    router.callback_query.register(change_preference, F.data.startswith('pref:'))
    return router
