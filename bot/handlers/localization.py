"""Locale context wraps admission errors; onboarding gates ordinary actions."""
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from bot.handlers.language import INVALID_CHOICE, LanguageCallback, show_language_choice
from bot.utils.i18n import LANGUAGES, language_context


def opens_language_selector(event) -> bool:
    if not isinstance(event, Message) or not event.text:
        return False
    command = event.text.strip().split(maxsplit=1)[0].split('@', 1)[0].lower()
    return command in {'/start', 'start', '/language', '/restart'}


class LanguageMiddleware(BaseMiddleware):
    def __init__(self, store):
        self.store = store

    async def __call__(self, handler, event, data):
        incoming = event.event
        actor = getattr(incoming, 'from_user', None)
        language = await self.store.get_language(actor.id) if actor else None
        data['selected_language'] = language
        data['language_selection'] = False
        # A current, owner-bound selector is a one-shot lightweight action, not
        # another download. It must work immediately after /start's cooldown.
        if isinstance(incoming, CallbackQuery) and incoming.message is not None and actor:
            try:
                choice = LanguageCallback.unpack(incoming.data or '')
                if choice.code in LANGUAGES:
                    data['language_selection'] = await self.store.language_choice_valid(
                        actor.id, incoming.message.chat.id, incoming.message.message_id, choice.token)
            except (ValueError, TypeError):
                pass
        with language_context(language or 'ro'):
            return await handler(event, data)


class RequireLanguageMiddleware(BaseMiddleware):
    def __init__(self, store):
        self.store = store

    async def __call__(self, handler, event, data):
        incoming = event.event
        if (getattr(incoming, 'from_user', None) is None or data.get('selected_language')
                or opens_language_selector(incoming)
                or isinstance(incoming, CallbackQuery) and (incoming.data or '').startswith(('language:', 'restart:'))):
            return await handler(event, data)
        if isinstance(incoming, Message):
            await show_language_choice(incoming, self.store)
        elif isinstance(incoming, CallbackQuery):
            await incoming.answer(INVALID_CHOICE, show_alert=True)
        return None
