from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from bot.utils.telegram_retry import TelegramRetryMiddleware


@pytest.mark.asyncio
async def test_retry_after_applies_to_control_messages_and_subsequent_requests() -> None:
    now, sleeps = [0.0], []

    async def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    middleware = TelegramRetryMiddleware(timeout=5, clock=lambda: now[0], sleep=sleep)
    method = SendMessage(chat_id=1, text="progress")
    rejected = AsyncMock(side_effect=TelegramRetryAfter(method=method, message="wait", retry_after=7))
    with pytest.raises(TelegramRetryAfter):
        await middleware(rejected, SimpleNamespace(), method)
    assert rejected.await_count == 3
    accepted = AsyncMock(return_value=True)
    assert await middleware(accepted, SimpleNamespace(), method) is True
    assert sleeps == [7, 7, 7]


@pytest.mark.asyncio
async def test_uncertain_telegram_network_failure_is_not_retried() -> None:
    method = SendMessage(chat_id=1, text="progress")
    request = AsyncMock(side_effect=TelegramNetworkError(method=method, message="unknown delivery"))
    with pytest.raises(TelegramNetworkError):
        await TelegramRetryMiddleware()(request, SimpleNamespace(), method)
    request.assert_awaited_once()
