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


@pytest.mark.asyncio
async def test_chat_cooldown_serializes_its_sends_without_stalling_other_scopes():
    import asyncio
    from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetUpdates

    now = [0.0]
    sleeping, release = asyncio.Event(), asyncio.Event()
    seen = []
    async def sleep(delay):
        sleeping.set()
        await release.wait()
        now[0] += delay
    middleware = TelegramRetryMiddleware(clock=lambda: now[0], sleep=sleep)
    first = SendMessage(chat_id=1, text='first')
    rejected = False
    async def transport(bot, method):
        nonlocal rejected
        seen.append(method)
        if method is first and not rejected:
            rejected = True
            raise TelegramRetryAfter(method=method, message='wait', retry_after=7)
        return True

    owner = asyncio.create_task(middleware(transport, SimpleNamespace(), first))
    await sleeping.wait()
    edit = EditMessageText(chat_id=1, message_id=20, text='same chat')
    queued = asyncio.create_task(middleware(transport, SimpleNamespace(), edit))
    independent = [SendMessage(chat_id=2, text='other chat'), GetUpdates(),
                   AnswerCallbackQuery(callback_query_id='callback')]
    try:
        for method in independent:
            assert await asyncio.wait_for(middleware(transport, SimpleNamespace(), method), .2)
        await asyncio.sleep(0)
        assert edit not in seen
        release.set()
        assert await owner and await queued
        assert seen == [first, *independent, first, edit]
    finally:
        owner.cancel()
        queued.cancel()
        await asyncio.gather(owner, queued, return_exceptions=True)


@pytest.mark.asyncio
async def test_scope_capacity_prunes_only_expired_idle_entries():
    import asyncio
    from bot.models.exceptions import QueueFullError
    now = [0.0]
    middleware = TelegramRetryMiddleware(capacity=2, idle_ttl=5, clock=lambda: now[0])
    entered, release = asyncio.Event(), asyncio.Event()
    async def transport(bot, method):
        if method.chat_id == 1:
            entered.set()
            await release.wait()
        return method.chat_id
    active = asyncio.create_task(middleware(transport, SimpleNamespace(), SendMessage(chat_id=1, text='held')))
    await entered.wait()
    try:
        assert await middleware(transport, SimpleNamespace(), SendMessage(chat_id=2, text='idle')) == 2
        with pytest.raises(QueueFullError):
            await middleware(transport, SimpleNamespace(), SendMessage(chat_id=3, text='full'))
        for chat in range(3, 30):
            now[0] += 6
            assert await middleware(transport, SimpleNamespace(), SendMessage(chat_id=chat, text='pruned')) == chat
        # Active chat survives every pruning pass and still shares its lock.
        follower = asyncio.create_task(middleware(transport, SimpleNamespace(), SendMessage(chat_id=1, text='queued')))
        await asyncio.sleep(0)
        assert not follower.done()
        release.set()
        assert await active == await follower == 1
    finally:
        release.set()
        active.cancel()
        await asyncio.gather(active, return_exceptions=True)


@pytest.mark.asyncio
async def test_deadline_includes_lock_wait_and_cancellation_releases_scope():
    import asyncio
    entered = asyncio.Event()
    async def transport(bot, method):
        if method.text == 'hold':
            entered.set()
            await asyncio.Event().wait()
        return method.text
    middleware = TelegramRetryMiddleware(timeout=.03)
    held = asyncio.create_task(middleware(transport, SimpleNamespace(), SendMessage(chat_id=1, text='hold')))
    await entered.wait()
    # A waiter gets its own deadline, including all time spent behind the first request.
    middleware.timeout = .01
    with pytest.raises(TimeoutError):
        await middleware(transport, SimpleNamespace(), SendMessage(chat_id=1, text='queued'))
    held.cancel()
    await asyncio.gather(held, return_exceptions=True)
    assert await middleware(transport, SimpleNamespace(), SendMessage(chat_id=1, text='after')) == 'after'


@pytest.mark.asyncio
async def test_timed_out_retry_preserves_cooldown_until_its_deadline():
    import asyncio
    from bot.models.exceptions import QueueFullError
    now = [0.0]
    async def blocked_sleep(delay):
        await asyncio.Event().wait()
    middleware = TelegramRetryMiddleware(timeout=.01, capacity=1, idle_ttl=1,
        clock=lambda: now[0], sleep=blocked_sleep)
    method = SendMessage(chat_id=1, text='rejected')
    async def reject(bot, method):
        raise TelegramRetryAfter(method=method, message='wait', retry_after=7)
    with pytest.raises(TimeoutError):
        await middleware(reject, SimpleNamespace(), method)
    now[0] = 2
    accepted = AsyncMock(return_value=True)
    with pytest.raises(QueueFullError):
        await middleware(accepted, SimpleNamespace(), SendMessage(chat_id=2, text='full'))
    with pytest.raises(TimeoutError):
        await middleware(accepted, SimpleNamespace(), method)
    accepted.assert_not_awaited()
    now[0] = 8
    assert await middleware(accepted, SimpleNamespace(), SendMessage(chat_id=2, text='expired'))


@pytest.mark.asyncio
async def test_canceled_waiter_cannot_evict_or_unlock_an_active_chat():
    import asyncio
    from bot.models.exceptions import QueueFullError
    now = [0.0]
    entered, release = asyncio.Event(), asyncio.Event()
    middleware = TelegramRetryMiddleware(capacity=1, idle_ttl=5, clock=lambda: now[0])
    seen = []
    async def transport(bot, method):
        seen.append(method.text)
        if method.text == 'hold':
            entered.set()
            await release.wait()
        return True
    held = asyncio.create_task(middleware(transport, SimpleNamespace(), SendMessage(chat_id=1, text='hold')))
    await entered.wait()
    waiter = asyncio.create_task(middleware(transport, SimpleNamespace(), SendMessage(chat_id=1, text='cancel')))
    try:
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        now[0] = 100
        with pytest.raises(QueueFullError):
            await middleware(transport, SimpleNamespace(), SendMessage(chat_id=2, text='new'))
        assert seen == ['hold']
        release.set()
        assert await held
        assert await middleware(transport, SimpleNamespace(), SendMessage(chat_id=1, text='after'))
        now[0] = 106
        assert await middleware(transport, SimpleNamespace(), SendMessage(chat_id=2, text='new'))
        assert seen == ['hold', 'after', 'new']
    finally:
        held.cancel()
        waiter.cancel()
        await asyncio.gather(held, waiter, return_exceptions=True)
