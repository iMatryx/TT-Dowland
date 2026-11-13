"""Real dispatcher flow; Telegram transport and source service stay offline."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, SendMessage, SetMyCommands
from aiogram.types import Message, Chat, Update

from bot.config import Settings
from bot.telegram_config import create_dispatcher


@pytest.fixture
async def app(tmp_path):
    calls = []
    async def request(client, method, timeout=None):
        calls.append(method)
        if isinstance(method, (AnswerCallbackQuery, SetMyCommands)):
            return True
        return Message(message_id=len(calls) + 100, date=datetime.now(timezone.utc),
                       chat=Chat(id=method.chat_id, type='private'))
    bot = Bot('123456:TEST_ONLY_NOT_A_REAL_TOKEN', session=AsyncMock(side_effect=request))
    dispatcher = create_dispatcher(Settings(DOWNLOAD_DIR=tmp_path / 'downloads', CACHE_DB_PATH=tmp_path / 'db'))
    service = SimpleNamespace(analyze=AsyncMock(), download=AsyncMock())
    state = SimpleNamespace(bot=bot, dispatcher=dispatcher, calls=calls, service=service)
    try:
        yield state
    finally:
        await dispatcher['download_service'].engine.close()
        await dispatcher['media_details'].close()
        await bot.session.close()


async def message(app, text, user=11, chat=10):
    await app.dispatcher.feed_update(app.bot, Update.model_validate({'update_id': len(app.calls)+1,
        'message': {'message_id': 1, 'date': 1, 'chat': {'id': chat, 'type': 'private'},
                    'from': {'id': user, 'is_bot': False, 'first_name': 'User'}, 'text': text}}),
        download_service=app.service)


async def choose(app, data, user=11, chat=10, message_id=None):
    if message_id is None:
        store = app.dispatcher['language_store']
        message_id = await store._call(lambda db: db.execute(
            'SELECT choice_message FROM user_languages WHERE user_id=?', (11,)).fetchone()[0])
    await app.dispatcher.feed_update(app.bot, Update.model_validate({'update_id': len(app.calls)+1,
        'callback_query': {'id': str(len(app.calls)), 'from': {'id': user, 'is_bot': False, 'first_name': 'User'},
            'chat_instance': 'offline', 'data': data, 'message': {'message_id': message_id, 'date': 1,
            'chat': {'id': chat, 'type': 'private'}, 'text': 'Language'}}}), download_service=app.service)


def selector(app, code):
    sent = next(c for c in reversed(app.calls) if isinstance(c, SendMessage))
    return next(b.callback_data for row in sent.reply_markup.inline_keyboard for b in row
                if b.callback_data.startswith(f'language:{code}:'))


async def test_first_contact_requires_selection_before_source_analysis(app):
    await message(app, 'https://www.tiktok.com/@u/video/123')
    app.service.analyze.assert_not_awaited()
    data = selector(app, 'en')
    await choose(app, data)
    assert await app.dispatcher['language_store'].get_language(11) == 'en'
    sent = next(c for c in reversed(app.calls) if isinstance(c, SendMessage))
    assert 'Send a public' in sent.text
    assert sent.reply_markup.keyboard[0][0].text == '📥 Download video'
    await message(app, '/help')
    assert 'How to use the bot' in app.calls[-1].text
    await message(app, '/start')
    assert await app.dispatcher['language_store'].get_language(11) is None
    second = selector(app, 'ru')
    await choose(app, second)
    assert 'Отправьте ссылку' in app.calls[-1].text
    assert app.calls[-1].reply_markup.keyboard[0][0].text == '📥 Скачать видео'


async def test_stale_and_other_user_selectors_do_not_change_language(app):
    await message(app, '/start')
    old = selector(app, 'en')
    await choose(app, old, user=22)
    assert await app.dispatcher['language_store'].get_language(22) is None
    assert await app.dispatcher['language_store'].get_language(11) is None
    await message(app, '/start')
    new = selector(app, 'ro')
    await choose(app, old)
    assert await app.dispatcher['language_store'].get_language(11) is None
    await choose(app, new)
    assert await app.dispatcher['language_store'].get_language(11) == 'ro'


async def test_old_menu_labels_use_current_selected_language(app):
    await message(app, '/start')
    await choose(app, selector(app, 'ru'))
    await message(app, '❓ Cum folosesc botul')
    assert 'Как пользоваться ботом' in app.calls[-1].text
    await message(app, '/menu')
    assert 'Отправьте ссылку' in app.calls[-1].text
    assert await app.dispatcher['language_store'].get_language(11) == 'ru'


async def test_valid_language_selection_works_during_cooldown_and_notices_use_chosen_language(app):
    from bot.handlers.limits import AdmissionMiddleware
    from bot.services.operations import UserLimiter
    runtime = SimpleNamespace(closing=False, limiter=UserLimiter(cooldown=2), handlers=set())
    app.dispatcher.update.outer_middleware(AdmissionMiddleware(runtime))
    await message(app, '/start')
    await choose(app, selector(app, 'en'))
    assert 'Send a public' in app.calls[-1].text
    await message(app, '/help')
    assert 'temporary limit' in app.calls[-1].text.lower()
    assert not runtime.handlers
    app.service.analyze.assert_not_awaited()


async def test_slash_menu_uses_selected_language_with_user_scoped_commands(app):
    await message(app, '/start')
    await choose(app, selector(app, 'en'))
    command_updates = [call for call in app.calls if isinstance(call, SetMyCommands)]
    assert len(command_updates) == 1
    update = command_updates[0]
    assert update.scope.type == 'chat' and update.scope.chat_id == 10
    assert update.language_code is None
    assert next(c.description for c in update.commands if c.command == 'help') == 'How to use the bot'


async def test_group_command_menu_is_scoped_to_the_selecting_member():
    from bot.handlers.commands import register_selected_commands
    from bot.utils.i18n import language_context
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    with language_context('ru'):
        await register_selected_commands(bot, Chat(id=-100, type='supergroup'), 22)
    call = bot.set_my_commands.call_args
    assert call.kwargs['scope'].type == 'chat_member'
    assert call.kwargs['scope'].user_id == 22 and call.kwargs['scope'].chat_id == -100
    assert next(c.description for c in call.args[0] if c.command == 'help') == 'Как пользоваться ботом'


async def test_language_command_reopens_selector_and_changes_saved_language(app):
    await message(app, '/start')
    await choose(app, selector(app, 'ro'))
    await message(app, '/language')
    assert await app.dispatcher['language_store'].get_language(11) is None
    await choose(app, selector(app, 'en'))
    assert await app.dispatcher['language_store'].get_language(11) == 'en'
    assert 'Send a public' in app.calls[-1].text
    commands = next(c for c in reversed(app.calls) if isinstance(c, SetMyCommands)).commands
    assert next(c.description for c in commands if c.command == 'language') == 'Change language'


async def test_restart_confirmation_is_immediate_but_only_for_private_admin(app):
    from bot.handlers.limits import AdmissionMiddleware
    from bot.services.operations import UserLimiter
    from bot.services.restart import RestartController
    control = RestartController(frozenset({11}), enabled=True)
    app.dispatcher['restart_controller'] = control
    runtime = SimpleNamespace(closing=False, limiter=UserLimiter(cooldown=2), handlers=set())
    app.dispatcher.update.outer_middleware(AdmissionMiddleware(runtime))
    await message(app, '/restart', user=11, chat=11)
    prompt = next(c for c in reversed(app.calls) if isinstance(c, SendMessage))
    data = prompt.reply_markup.inline_keyboard[0][0].callback_data
    message_id = control._pending[11].message_id
    await app.dispatcher.feed_update(app.bot, Update.model_validate({'update_id': 200,
        'callback_query': {'id':'restart-confirm','from':{'id':11,'is_bot':False,'first_name':'Admin'},
            'chat_instance':'offline','data':data,'message':{'message_id':message_id,'date':1,
                'chat':{'id':11,'type':'private'},'text':prompt.text}}}))
    assert control.requested.is_set()  # In-memory signal only; no lifecycle watcher runs here.
    app.service.analyze.assert_not_awaited()
    assert not runtime.handlers


async def test_non_admin_restart_is_denied_even_without_language_selection(app):
    from bot.services.restart import RestartController
    control = RestartController(frozenset({11}), enabled=True)
    app.dispatcher['restart_controller'] = control
    await message(app, '/restart', user=22, chat=22)
    assert 'doar administratorului' in app.calls[-1].text
    assert not control._pending and not control.requested.is_set()
    app.service.analyze.assert_not_awaited()
