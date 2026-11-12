"""Compact, opaque callback data; no URLs or paths are sent to clients."""

from typing import Literal

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class DownloadCallback(CallbackData, prefix="dl"):
    mode: Literal["video", "document"]
    key: str


def download_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📹 Descarcă video", callback_data=DownloadCallback(mode="video", key=key).pack())],
        [InlineKeyboardButton(text="📁 Descarcă original", callback_data=DownloadCallback(mode="document", key=key).pack())],
    ])
