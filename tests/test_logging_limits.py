import asyncio
import io
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers.limits import AdmissionMiddleware
from bot.services.operations import UserLimiter
from bot.utils.logging import SecretFilter


def test_logs_redact_token_in_message_and_traceback() -> None:
    token = "123456:TEST_TOKEN_ONLY_NEVER_REAL"
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(SecretFilter(token))
    logger = logging.Logger("isolated-redaction-test")
    logger.addHandler(handler)
    try:
        raise RuntimeError(f"Authorization: Bearer {token}")
    except RuntimeError:
        logger.exception("Request /bot%s failed", token)
    text = output.getvalue()
    assert token not in text
    assert "[REDACTED]" in text


@pytest.mark.asyncio
async def test_middleware_cooldown_suppresses_repeated_spam_notices() -> None:
    runtime = SimpleNamespace(closing=False, limiter=UserLimiter(cooldown=3), handlers=set())
    middleware = AdmissionMiddleware(runtime)
    message = SimpleNamespace(from_user=SimpleNamespace(id=1), answer=AsyncMock())
    update = SimpleNamespace(event=message)
    handler = AsyncMock(return_value="accepted")
    assert await middleware(handler, update, {}) == "accepted"
    await middleware(handler, update, {})
    await middleware(handler, update, {})
    handler.assert_awaited_once()
    message.answer.assert_awaited_once()
    assert "limita temporară" in message.answer.call_args.args[0]
    assert not runtime.handlers
