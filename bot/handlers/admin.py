"""Private, allowlisted restart command with expiring bound confirmation."""
from typing import Literal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InaccessibleMessage, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.services.restart import RestartController
from bot.utils.i18n import tr


class RestartCallback(CallbackData, prefix='restart'):
    action: Literal['confirm', 'cancel']
    token: str


def private_admin(actor, message, controller: RestartController) -> bool:
    return bool(actor is not None and not actor.is_bot and message is not None
        and not isinstance(message, InaccessibleMessage) and message.chat.type == 'private'
        and message.chat.id == actor.id and getattr(message, 'sender_chat', None) is None
        and controller.is_admin(actor.id))


def valid_confirmation(query: CallbackQuery, controller: RestartController) -> bool:
    if not private_admin(query.from_user, query.message, controller):
        return False
    try:
        choice = RestartCallback.unpack(query.data or '')
    except (ValueError, TypeError):
        return False
    return controller.valid(query.from_user.id, query.message.chat.id, query.message.message_id, choice.token)


async def restart_handler(message: Message, restart_controller: RestartController) -> None:
    if not private_admin(message.from_user, message, restart_controller) or message.sender_chat is not None:
        await message.answer(tr('🔒 Această comandă este disponibilă doar administratorului, în chat privat.'))
        return
    if not restart_controller.enabled:
        await message.answer(tr('Repornirea este dezactivată. Administratorul trebuie să configureze supervisorul pe server.'))
        return
    try:
        token = restart_controller.begin(message.from_user.id, message.chat.id)
    except PermissionError:
        await message.answer(tr('Repornirea este deja în curs sau indisponibilă.'))
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr('🔄 Confirmă restart'), callback_data=RestartCallback(action='confirm', token=token).pack()),
        InlineKeyboardButton(text=tr('❌ Anulează'), callback_data=RestartCallback(action='cancel', token=token).pack()),
    ]])
    prompt = await message.answer(tr('🔄 Repornești botul pentru toți utilizatorii? Operațiile neterminate pot fi întrerupte. Confirmarea expiră în 60 de secunde.'), reply_markup=keyboard)
    restart_controller.bind(message.from_user.id, token, prompt.message_id)


async def restart_callback(query: CallbackQuery, restart_controller: RestartController) -> None:
    if not valid_confirmation(query, restart_controller):
        await query.answer(tr('Confirmare invalidă, expirată sau neautorizată.'), show_alert=True)
        return
    choice = RestartCallback.unpack(query.data)
    user, chat, message = query.from_user.id, query.message.chat.id, query.message.message_id
    if choice.action == 'cancel':
        if not restart_controller.cancel(user, chat, message, choice.token):
            await query.answer(tr('Confirmare invalidă, expirată sau neautorizată.'), show_alert=True)
            return
        await query.answer()
        await query.message.edit_text(tr('Restart anulat.'), reply_markup=None)
        return
    if not restart_controller.consume(user, chat, message, choice.token):
        await query.answer(tr('Confirmare invalidă, expirată sau neautorizată.'), show_alert=True)
        return
    # No restart is requested if Telegram fails before the acknowledgment.
    await query.answer()
    await query.message.edit_text(tr('🔄 Botul se închide controlat și va fi repornit de supervisor.'), reply_markup=None)
    restart_controller.request_restart()


def create_router() -> Router:
    router = Router(name='administrator')
    router.message.register(restart_handler, Command('restart'))
    router.callback_query.register(restart_callback, F.data.startswith('restart:'))
    return router
