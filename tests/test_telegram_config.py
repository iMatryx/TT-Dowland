from unittest.mock import AsyncMock

import pytest

from bot.config import Settings
from bot.main import run_polling
from bot.models.exceptions import ConfigurationError
from bot import telegram_config


def test_dispatcher_is_created_offline_without_token() -> None:
    dispatcher = telegram_config.create_dispatcher(Settings())
    assert set(dispatcher.resolve_used_update_types()) == {"message", "callback_query"}


@pytest.mark.parametrize("token", [None, "", "not-a-token"])
def test_polling_client_requires_valid_token(token: str | None) -> None:
    with pytest.raises(ConfigurationError):
        telegram_config.create_bot(Settings(BOT_TOKEN=token))


@pytest.mark.asyncio
async def test_polling_closes_session_after_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    class FakeBot:
        session = type("Session", (), {"close": AsyncMock()})()

    bot = FakeBot()
    dispatcher = type("Dispatcher", (), {
        "resolve_used_update_types": lambda self: ["message", "callback_query"],
        "start_polling": AsyncMock(side_effect=RuntimeError("network unavailable")),
    })()
    monkeypatch.setattr(telegram_config, "create_bot", lambda settings: bot)
    monkeypatch.setattr(telegram_config, "create_dispatcher", lambda settings, runtime=None: dispatcher)
    with pytest.raises(RuntimeError):
        await run_polling(Settings(DOWNLOAD_DIR=tmp_path, CACHE_DB_PATH=tmp_path / "cache.sqlite3"))
    bot.session.close.assert_awaited_once()
    assert dispatcher.start_polling.call_args.kwargs["close_bot_session"] is False
