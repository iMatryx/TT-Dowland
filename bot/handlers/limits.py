"""Bounded per-user admission and active-update ownership for shutdown."""

import asyncio
import logging
from typing import Any
from collections.abc import Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from bot.models.exceptions import QueueFullError, RateLimitedError
from bot.utils.telegram_text import error_text

logger = logging.getLogger(__name__)


class AdmissionMiddleware(BaseMiddleware):
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    async def __call__(self, handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
                       event: Any, data: dict[str, Any]) -> Any:
        incoming = event.event
        actor = getattr(incoming, "from_user", None)
        if actor is None:
            return await handler(event, data)
        acquired = False
        task = asyncio.current_task()
        try:
            if self.runtime.closing:
                raise QueueFullError("Service is stopping")
            self.runtime.limiter.acquire(actor.id)
            acquired = True
            self.runtime.handlers.add(task)
            return await handler(event, data)
        except (RateLimitedError, QueueFullError) as exc:
            if isinstance(exc, RateLimitedError) and not exc.notify:
                return None
            try:
                if isinstance(incoming, CallbackQuery):
                    await incoming.answer(error_text(exc), show_alert=True)
                else:
                    await incoming.answer(error_text(exc), parse_mode="HTML")
            except TelegramAPIError:
                logger.warning("Could not send admission-limit notice")
            return None
        finally:
            if acquired:
                self.runtime.limiter.release(actor.id)
                self.runtime.handlers.discard(task)
