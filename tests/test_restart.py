"""Restart authorization and lifecycle signals; never exit or start a real bot."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from bot.config import Settings


def test_restart_configuration_fails_closed_and_hides_allowlist():
    settings = Settings()
    assert settings.administrator_ids == frozenset()
    assert settings.restart_enabled is False
    configured = Settings(ADMIN_USER_IDS='123456789,987654321')
    assert configured.administrator_ids == frozenset({123456789, 987654321})
    assert '123456789' not in repr(configured)


@pytest.mark.parametrize('value', ['@owner', '0', '-10', '12,', '1,true', '1; reboot', '1,２'])
def test_restart_rejects_invalid_admin_configuration(value):
    with pytest.raises(ValidationError):
        Settings(ADMIN_USER_IDS=value)


async def test_restart_confirmation_is_scoped_expiring_and_single_use():
    from bot.services.restart import RestartController
    now = SimpleNamespace(value=0)
    control = RestartController(frozenset({11}), enabled=True, clock=lambda: now.value)
    token = control.begin(11, 11)
    assert control.bind(11, token, 20)
    for user, chat, msg in [(22,22,20), (11,-100,20), (11,11,21)]:
        assert not control.valid(user, chat, msg, token)
    assert control.valid(11, 11, 20, token)
    now.value = 61
    assert not control.consume(11, 11, 20, token)
    fresh = control.begin(11, 11)
    control.bind(11, fresh, 21)
    assert control.consume(11, 11, 21, fresh)
    assert not control.consume(11, 11, 21, fresh)
    assert not control.requested.is_set()
    assert control.request_restart()
    assert control.requested.is_set()
    assert not control.request_restart()


def test_restart_disabled_or_empty_allowlist_cannot_create_tokens():
    from bot.services.restart import RestartController
    for control in [RestartController(frozenset(), enabled=True), RestartController(frozenset({11}), enabled=False)]:
        with pytest.raises(PermissionError):
            control.begin(11, 11)
        assert not control.request_restart()


async def test_restart_lifecycle_closes_resources_then_returns_restart_exit_code(monkeypatch, tmp_path):
    import asyncio
    from bot import telegram_config
    from bot.main import run_polling
    from bot.services.restart import RESTART_EXIT_CODE
    stopped = asyncio.Event()
    captured = {}
    bot = SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))
    async def start_polling(*args, **kwargs):
        control = captured['runtime'].restart
        token = control.begin(11, 11)
        control.bind(11, token, 20)
        assert control.consume(11, 11, 20, token)
        assert control.request_restart()
        await stopped.wait()
    async def stop_polling():
        assert captured['runtime'].closing
        stopped.set()
    dispatcher = SimpleNamespace(start_polling=start_polling, stop_polling=AsyncMock(side_effect=stop_polling),
        resolve_used_update_types=lambda: ['message', 'callback_query'])
    def compose(settings, runtime=None):
        captured['runtime'] = runtime
        return dispatcher
    monkeypatch.setattr(telegram_config, 'create_bot', lambda settings: bot)
    monkeypatch.setattr(telegram_config, 'create_dispatcher', compose)
    settings = Settings(DOWNLOAD_DIR=tmp_path / 'downloads', CACHE_DB_PATH=tmp_path / 'db',
                        ADMIN_USER_IDS='11', RESTART_ENABLED=True, SHUTDOWN_GRACE_SECONDS=.01)
    assert await asyncio.wait_for(run_polling(settings), 1) == RESTART_EXIT_CODE
    dispatcher.stop_polling.assert_awaited_once()
    bot.session.close.assert_awaited_once()
    assert captured['runtime']._cleaner_task is None
    assert captured['runtime'].media_details._closed


def admin_message(user=11, chat=None, chat_type='private', **extra):
    actor = SimpleNamespace(id=user, is_bot=False, username='same_name') if user is not None else None
    return SimpleNamespace(from_user=actor, chat=SimpleNamespace(id=chat if chat is not None else user, type=chat_type),
        sender_chat=None, message_id=20, answer=AsyncMock(return_value=SimpleNamespace(message_id=20)),
        edit_text=AsyncMock(), **extra)


@pytest.mark.parametrize('user,chat,kind', [(22,22,'private'), (11,-100,'supergroup'), (None,11,'private'), (11,22,'private')])
async def test_restart_handler_denies_impostors_and_groups(user, chat, kind):
    from bot.handlers.admin import restart_handler
    from bot.services.restart import RestartController
    control = RestartController(frozenset({11}), enabled=True)
    message = admin_message(user, chat, kind)
    await restart_handler(message, control)
    assert message.answer.call_args.kwargs.get('reply_markup') is None
    assert not control._pending and not control.requested.is_set()


async def test_restart_requires_confirmation_and_cancel_never_requests_restart():
    from bot.handlers.admin import restart_handler, restart_callback
    from bot.services.restart import RestartController
    control = RestartController(frozenset({11}), enabled=True)
    message = admin_message()
    await restart_handler(message, control)
    buttons = message.answer.call_args.kwargs['reply_markup'].inline_keyboard[0]
    assert not control.requested.is_set()
    query = SimpleNamespace(from_user=message.from_user, message=message, data=buttons[1].callback_data, answer=AsyncMock())
    await restart_callback(query, control)
    assert not control.requested.is_set()
    query.data = buttons[0].callback_data
    await restart_callback(query, control)
    assert not control.requested.is_set()
    assert query.answer.call_args.kwargs['show_alert']


async def test_only_current_admin_can_confirm_once_and_failed_ack_does_not_restart():
    from bot.handlers.admin import restart_handler, restart_callback
    from bot.services.restart import RestartController
    control = RestartController(frozenset({11}), enabled=True)
    message = admin_message()
    await restart_handler(message, control)
    data = message.answer.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
    query = SimpleNamespace(from_user=SimpleNamespace(id=22,is_bot=False), message=message, data=data, answer=AsyncMock())
    await restart_callback(query, control)
    assert not control.requested.is_set()
    query.from_user = message.from_user
    message.edit_text.side_effect = RuntimeError('offline send failure')
    with pytest.raises(RuntimeError):
        await restart_callback(query, control)
    assert not control.requested.is_set()
    message.edit_text.side_effect = None
    await restart_handler(message, control)
    query.data = message.answer.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
    await restart_callback(query, control)
    assert control.requested.is_set()
    await restart_callback(query, control)
    assert query.answer.call_args.kwargs['show_alert']


@pytest.mark.parametrize('data', ['restart:confirm:💥', 'restart:confirm:bad:extra', 'restart:run:bad'])
async def test_forged_restart_callback_never_requests_restart(data):
    from bot.handlers.admin import restart_callback
    from bot.services.restart import RestartController
    control = RestartController(frozenset({11}), enabled=True)
    token = control.begin(11, 11)
    control.bind(11, token, 20)
    query = SimpleNamespace(from_user=SimpleNamespace(id=11,is_bot=False), message=admin_message(),
                            data=data, answer=AsyncMock())
    await restart_callback(query, control)
    assert query.answer.call_args.kwargs['show_alert']
    assert not control.requested.is_set()


async def test_restart_command_is_only_shown_in_private_admin_menu():
    from bot.handlers.commands import interface_commands, register_selected_commands
    from aiogram.types import Chat
    assert 'restart' not in [c.command for c in interface_commands()]
    settings = Settings(ADMIN_USER_IDS='11', RESTART_ENABLED=True)
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    for user, kind, expected in [(11,'private',True), (22,'private',False), (11,'supergroup',False)]:
        await register_selected_commands(bot, Chat(id=user if kind=='private' else -100,type=kind), user, settings)
        assert ('restart' in [c.command for c in bot.set_my_commands.call_args.args[0]]) is expected
