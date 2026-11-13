"""Allowlisted JSON performance events; never serialize request or exception data."""

import asyncio
from collections import Counter
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import json
import logging
import math
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4

from bot.services.urls import TIKTOK_HOSTS, YOUTUBE_HOSTS, INSTAGRAM_HOSTS

logger = logging.getLogger('bot.performance')


@dataclass(frozen=True)
class Operation:
    operation_id: str
    platform: str


_current: ContextVar[Operation | None] = ContextVar('performance_operation', default=None)


def platform_for(url: str) -> str:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return 'unknown'
    for name, hosts in [('tiktok', TIKTOK_HOSTS), ('youtube', YOUTUBE_HOSTS), ('instagram', INSTAGRAM_HOSTS)]:
        if host in hosts:
            return name
    return 'unknown'


@contextmanager
def operation(url: str = ''):
    """Reuse the owner's context across nested calls and spawned shared tasks."""
    token = None
    if _current.get() is None:
        token = _current.set(Operation(uuid4().hex, platform_for(url)))
    try:
        yield _current.get()
    finally:
        if token is not None:
            _current.reset(token)


def _source(args, kwargs) -> str:
    for value in (*args, *kwargs.values()):
        info = getattr(value, 'video_info', value)
        url = getattr(info, 'webpage_url', None)
        if url is not None:
            return str(url)
        if isinstance(value, str) and value.startswith(('https://', 'http://')):
            return value
    return ''


def scoped(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        with operation(_source(args, kwargs)):
            return await function(*args, **kwargs)
    return wrapped


def emit(event: str, context: Operation, **fields) -> None:
    logger.info(json.dumps({'event': event, 'operation_id': context.operation_id,
                            'platform': context.platform, **fields}, separators=(',', ':')))


class Stage:
    def __init__(self, name: str, *, queue: str | None = None) -> None:
        self.name = name
        self.queue = queue
        self.context = _current.get() or Operation(uuid4().hex, 'unknown')
        self.started = time.monotonic()
        self.finished = False
        self.excluded_seconds = 0.0

    def finish(self, result: str) -> None:
        if not self.finished:
            self.finished = True
            emit('performance_stage', self.context, stage=self.name,
                 duration_seconds=max(0.0, time.monotonic() - self.started - self.excluded_seconds),
                 result=result, **({'queue': self.queue} if self.queue else {}))


@contextmanager
def stage(name: str, *, queue: str | None = None):
    measurement = Stage(name, queue=queue)
    try:
        yield measurement
    except asyncio.CancelledError:
        measurement.finish('cancelled')
        raise
    except BaseException:
        measurement.finish('error')
        raise
    else:
        measurement.finish('success')


def measured(name: str):
    def decorate(function):
        @wraps(function)
        @scoped
        async def wrapped(*args, **kwargs):
            with stage(name):
                return await function(*args, **kwargs)
        return wrapped
    return decorate


class CacheMetrics:
    """Process-local counters per cache and platform; one event per logical lookup."""
    def __init__(self, name: str) -> None:
        self.name = name
        self._counts: Counter = Counter()

    def record(self, hit: bool) -> None:
        context = _current.get() or Operation(uuid4().hex, 'unknown')
        self._counts[context.platform, hit] += 1
        hits = self._counts[context.platform, True]
        misses = self._counts[context.platform, False]
        emit('cache_lookup', context, cache=self.name, outcome='hit' if hit else 'miss',
             hits=hits, misses=misses, hit_rate=hits / (hits + misses))


@asynccontextmanager
async def queued_slot(semaphore, *, queue: str):
    with stage('queue_wait', queue=queue):
        await semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()


@contextmanager
def download_stage(timings_path: Path):
    """Subtract isolated worker postprocessing from the download wall time."""
    with stage('download') as measurement:
        result = 'success'
        try:
            yield
        except asyncio.CancelledError:
            result = 'cancelled'
            raise
        except BaseException:
            result = 'error'
            raise
        finally:
            try:
                if timings_path.is_file() and timings_path.stat().st_size <= 65536:
                    records = json.loads(timings_path.read_text(encoding='utf-8'))
                    end = time.monotonic()
                    for record in records:
                        start = float(record['started'])
                        finish = float(record.get('finished', end))
                        if not (math.isfinite(start) and math.isfinite(finish)):
                            continue
                        duration = max(0.0, min(end, finish) - max(measurement.started, start))
                        outcome = record.get('result', result)
                        if outcome not in {'success', 'error', 'cancelled'}:
                            continue
                        measurement.excluded_seconds += duration
                        emit('performance_stage', measurement.context, stage='conversion',
                             duration_seconds=duration, result=outcome)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                # Missing diagnostics must never change download/delivery behavior.
                pass
