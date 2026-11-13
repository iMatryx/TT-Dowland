"""Opaque owner-bound actions based on the media actually available."""

from typing import Literal
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.models import VideoInfo
from bot.services.quality import select_audio, select_formats
from bot.models.exceptions import VideoUnavailableError, SourceVariantUnavailableError


class DownloadCallback(CallbackData, prefix='dl'):
    mode: Literal['chat', 'file', 'video', 'document', 'clean', 'clean_document', 'maximum', 'photos', 'album', 'zip', 'audio']
    key: str


class PendingInfoCallback(CallbackData, prefix='pi'):
    action: Literal['show', 'hide']
    key: str


class VideoActionCallback(CallbackData, prefix='va'):
    mode: Literal['file', 'audio', 'zip']
    key: str


def delivered_video_keyboard(key: str, info: VideoInfo, *, max_bytes: int, preferences=None):
    allowed = dict(available_actions(info, max_bytes=max_bytes, preferences=preferences))
    row = []
    if 'file' in allowed:
        row.append(InlineKeyboardButton(text='📁 Ca fișier', callback_data=VideoActionCallback(mode='file', key=key).pack()))
    row.append(InlineKeyboardButton(text='ℹ️ Info', callback_data='mi:show'))
    rows = [row]
    extra = [InlineKeyboardButton(text=allowed[mode], callback_data=VideoActionCallback(mode=mode, key=key).pack())
             for mode in ('audio', 'zip') if mode in allowed]
    if extra:
        rows.append(extra)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def available_actions(info: VideoInfo, *, max_bytes: int | None = None, preferences=None) -> list[tuple[str, str]]:
    from bot.models.preferences import UserPreferences
    from bot.services.quality import select_photos
    options = preferences or UserPreferences()
    video_error = None
    if info.media_type == 'photo':
        if not info.photos:
            return []
        select_photos(info, quality=options.quality, watermark=options.watermark)
        known_size = 0
    else:
        try:
            selection = select_formats(info.formats, quality=options.quality, watermark=options.watermark)
            known_size = selection.known_size_bytes
        except SourceVariantUnavailableError as exc:
            video_error = exc
            known_size = None
    actions = []
    if known_size is not None and (max_bytes is None or known_size <= max_bytes):
        actions.extend([('chat', '💬 În chat'), ('file', '📁 Ca fișier original')])
        if max_bytes is None or known_size + (512 if info.media_type == 'video' else 0) <= max_bytes:
            actions.append(('zip', '🗜 Ca ZIP'))
    if info.media_type == 'video':
        try:
            audio = select_audio(info.formats, quality=options.quality)
            if max_bytes is None or audio.filesize_estimated or (audio.filesize or 0) <= max_bytes:
                actions.append(('audio', '🎵 Doar audio'))
        except VideoUnavailableError:
            pass
    if not actions and video_error is not None:
        raise video_error
    return actions


def download_keyboard(key: str, info: VideoInfo | None = None, *, max_bytes: int | None = None, preferences=None,
                      expanded: bool = False) -> InlineKeyboardMarkup:
    actions = available_actions(info, max_bytes=max_bytes, preferences=preferences) if info is not None else [('chat', '💬 În chat'), ('file', '📁 Ca fișier original'), ('zip', '🗜 Ca ZIP')]
    rows = [[InlineKeyboardButton(text=label, callback_data=DownloadCallback(mode=mode, key=key).pack())]
            for mode, label in actions]
    if rows:
        if info is not None:
            rows.append([InlineKeyboardButton(text='🙈 Ascunde info' if expanded else 'ℹ️ Info',
                callback_data=PendingInfoCallback(action='hide' if expanded else 'show', key=key).pack())])
        rows.append([InlineKeyboardButton(text='❌ Anulează', callback_data=CancelCallback(key=key).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


MENU_DOWNLOAD = '📥 Descarcă video'
MENU_PREFERENCES = '⚙️ Preferințe'
MENU_HELP = '❓ Cum folosesc botul'
MENU_LIMITS = 'ℹ️ Limite și calitate'


def main_menu():
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=MENU_DOWNLOAD), KeyboardButton(text=MENU_PREFERENCES)],
                  [KeyboardButton(text=MENU_HELP), KeyboardButton(text=MENU_LIMITS)]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder='TikTok, YouTube Shorts sau Instagram Reels…')


class PreferenceCallback(CallbackData, prefix='pref'):
    owner: int
    field: str
    value: str


class CancelCallback(CallbackData, prefix='cancel'):
    key: str


def preferences_keyboard(user_id, preferences):
    return InlineKeyboardMarkup(inline_keyboard=[])


def media_info_keyboard(*, expanded: bool = False, photo: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text='🙈 Ascunde info' if expanded else 'ℹ️ Info foto' if photo else 'ℹ️ Info video',
        callback_data='mi:hide' if expanded else 'mi:show')]])
