"""Independent process-wide budget for Telegram preparation and audio encoding."""

import asyncio
from contextlib import asynccontextmanager

from bot.utils.performance import queued_slot


class ConversionLimiter:
    def __init__(self, concurrency: int = 1) -> None:
        if concurrency < 1:
            raise ValueError('Conversion concurrency must be positive')
        self._slots = asyncio.Semaphore(concurrency)

    @asynccontextmanager
    async def slot(self):
        async with queued_slot(self._slots, queue='conversion'):
            yield
