"""One retry layer for every Bot API method, with shared RetryAfter cooldown."""

import asyncio
from collections.abc import Awaitable, Callable
import time
from typing import Any

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import TelegramMethod

from bot.services.operations import retry_async


class TelegramRetryMiddleware(BaseRequestMiddleware):
    def __init__(self, *, timeout: float = 120, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.timeout, self.clock, self.sleep = timeout, clock, sleep
        self._not_before = 0.0

    async def __call__(self, make_request: NextRequestMiddlewareType, bot: Bot,
                       method: TelegramMethod) -> Any:
        async def request():
            while (remaining := self._not_before - self.clock()) > 0:
                await self.sleep(remaining)
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                self._not_before = max(self._not_before, self.clock() + exc.retry_after)
                raise

        async with asyncio.timeout(self.timeout):
            return await retry_async(request, sleep=self.sleep, retry_network=False)
