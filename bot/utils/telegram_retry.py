"""Bounded retry scheduling by chat, with independent non-chat method scopes.

Telegram's retry_after alone does not identify a bot-wide limit. We apply every
rejection to its chat (including edits), or to its non-chat API method. Polling
and callback answers therefore never wait behind a chat's upload cooldown.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import time
from typing import Any

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import TelegramMethod

from bot.models.exceptions import QueueFullError
from bot.services.operations import retry_async


@dataclass
class _Scope:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0
    not_before: float = 0.0
    expires: float = 0.0


class TelegramRetryMiddleware(BaseRequestMiddleware):
    def __init__(self, *, timeout: float = 120, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 capacity: int = 4096, idle_ttl: float = 300) -> None:
        if capacity < 1 or idle_ttl < 0:
            raise ValueError('Retry scope capacity must be positive and expiry nonnegative')
        self.timeout, self.clock, self.sleep = timeout, clock, sleep
        self.capacity, self.idle_ttl = capacity, idle_ttl
        self._scopes: dict[tuple, _Scope] = {}

    def _acquire_scope(self, bot: Bot, method: TelegramMethod) -> _Scope:
        chat_id = getattr(method, 'chat_id', None)
        key = (getattr(bot, 'id', None), 'chat', str(chat_id)) if chat_id is not None else (
            getattr(bot, 'id', None), 'method', method.__api_method__)
        scope = self._scopes.get(key)
        if scope is None:
            if len(self._scopes) >= self.capacity:
                now = self.clock()
                self._scopes = {key: value for key, value in self._scopes.items()
                                if value.users or max(value.expires, value.not_before) > now}
            if len(self._scopes) >= self.capacity:
                raise QueueFullError('Telegram retry scope capacity reached.')
            scope = self._scopes[key] = _Scope()
        # Count holders and lock waiters, so pruning can never split their lock.
        scope.users += 1
        return scope

    async def __call__(self, make_request: NextRequestMiddlewareType, bot: Bot,
                       method: TelegramMethod) -> Any:
        async with asyncio.timeout(self.timeout):
            scope = self._acquire_scope(bot, method)
            try:
                async with scope.lock:
                    async def request():
                        while (remaining := scope.not_before - self.clock()) > 0:
                            await self.sleep(remaining)
                        try:
                            return await make_request(bot, method)
                        except TelegramRetryAfter as exc:
                            scope.not_before = max(scope.not_before, self.clock() + exc.retry_after)
                            raise

                    return await retry_async(request, sleep=self.sleep, retry_network=False)
            finally:
                scope.users -= 1
                scope.expires = self.clock() + self.idle_ttl
