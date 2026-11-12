"""Edit one status message; replace it only when Telegram refuses editing."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message


class ProgressMessage:
    def __init__(self, message: Message) -> None:
        self.message = message

    async def update(self, text: str, *, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        try:
            await self.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except TelegramBadRequest as exc:
            if "message is not modified" in exc.message.lower():
                return
            self.message = await self.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
