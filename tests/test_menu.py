from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.services.cache import SQLiteCache


@pytest.mark.asyncio
async def test_start_has_persistent_menu():
    from bot.handlers.commands import welcome_handler
    message = SimpleNamespace(answer=AsyncMock())
    await welcome_handler(message)
    keyboard = message.answer.call_args.kwargs['reply_markup']
    assert keyboard.is_persistent and keyboard.resize_keyboard
    assert [b.text for row in keyboard.keyboard for b in row] == [
        '📥 Descarcă video', '❓ Cum folosesc botul', 'ℹ️ Limite și calitate']


@pytest.fixture
async def menu_app(tmp_path):
    from datetime import datetime, timezone
    from aiogram import Bot
    from aiogram.types import Message, Chat, InlineKeyboardMarkup
    from aiogram.methods import AnswerCallbackQuery, SendVideo, SendDocument
    from bot.config import Settings
    from bot.telegram_config import create_dispatcher

    calls = []
    session = AsyncMock()
    bot = Bot('123456:TEST_ONLY_NOT_A_REAL_TOKEN', session=session)

    async def request(client, method, timeout=None):
        calls.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        return Message(message_id=30 if isinstance(method, SendVideo) else 40 if isinstance(method, SendDocument) else getattr(method, 'message_id', None) or 20,
                       date=datetime.now(timezone.utc), chat=Chat(id=10, type='private'),
                       text=getattr(method, 'text', None), reply_markup=method.reply_markup if isinstance(getattr(method, 'reply_markup', None), InlineKeyboardMarkup) else None).as_(bot)

    session.side_effect = request
    dispatcher = create_dispatcher(Settings(DOWNLOAD_DIR=tmp_path, CACHE_DB_PATH=tmp_path / 'cache.sqlite3'))
    store = dispatcher['language_store']
    token = await store.begin_language_choice(11, 10)
    await store.bind_language_choice(11, token, 20)
    assert await store.select_language(11, 10, 20, token, 'ro')
    service = SimpleNamespace(analyze=AsyncMock(), download=AsyncMock(), max_download_bytes=50_000_000)
    state = SimpleNamespace(bot=bot, dispatcher=dispatcher, calls=calls, service=service)
    try:
        yield state
    finally:
        if 'media_details' in dispatcher.workflow_data:
            await dispatcher['media_details'].close()
        await bot.session.close()


async def feed_message(app, text):
    from aiogram.types import Update
    await app.dispatcher.feed_update(app.bot, Update.model_validate({'update_id': 1, 'message': {
        'message_id': 19, 'date': 1, 'chat': {'id': 10, 'type': 'private'},
        'from': {'id': 11, 'is_bot': False, 'first_name': 'Test'}, 'text': text,
    }}), download_service=app.service)


@pytest.mark.asyncio
async def test_link_shows_only_title_and_info_edits_same_message(menu_app):
    from aiogram.methods import EditMessageText, SendMessage
    from aiogram.types import Update
    from bot.models import VideoInfo, PhotoInfo
    menu_app.service.analyze.return_value = VideoInfo(id='123', title='Titlu <test>',
        uploader='Autor', duration_seconds=12, media_type='photo', webpage_url='https://www.tiktok.com/@author/photo/123',
        photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/photo.jpg'], width=1080, height=1920)])
    await feed_message(menu_app, 'https://www.tiktok.com/@author/video/123')
    edit = next(c for c in reversed(menu_app.calls) if isinstance(c, EditMessageText))
    assert edit.text == '<b>Titlu &lt;test&gt;</b>'
    info = next(b for row in edit.reply_markup.inline_keyboard for b in row if b.callback_data.startswith('pi:'))
    messages_before = sum(isinstance(c, SendMessage) for c in menu_app.calls)
    for action, expanded in [('show', True), ('hide', False)]:
        await menu_app.dispatcher.feed_update(menu_app.bot, Update.model_validate({'update_id': 2, 'callback_query': {
            'id': action, 'from': {'id': 11, 'is_bot': False, 'first_name': 'Test'},
            'chat_instance': 'test', 'data': f'pi:{action}:{info.callback_data.rsplit(":", 1)[1]}',
            'message': {'message_id': 20, 'date': 1, 'chat': {'id': 10, 'type': 'private'}, 'text': edit.text},
        }}), download_service=menu_app.service)
        updated = next(c for c in reversed(menu_app.calls) if isinstance(c, EditMessageText))
        assert updated.message_id == 20
        assert ('1080 × 1920' in updated.text) == expanded
        if expanded:
            assert '12' in updated.text and 'fotografii' in updated.text
        else:
            assert updated.text == '<b>Titlu &lt;test&gt;</b>'
        assert any(b.callback_data.startswith('dl:') for row in updated.reply_markup.inline_keyboard for b in row)
    assert sum(isinstance(c, SendMessage) for c in menu_app.calls) == messages_before
    menu_app.service.download.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_stages_edit_one_status_message(menu_app, tmp_path):
    from aiogram.methods import EditMessageText, SendMessage, SendVideo
    from aiogram.types import Update
    from bot.models import VideoInfo, VideoFormat, DownloadResult
    fmt = VideoFormat(has_watermark=False, format_id='play', ext='mp4', width=1080, height=1920,
                      has_audio=True, audio_codec='aac', video_codec='h264')
    info = VideoInfo(id='123', title='Titlu', webpage_url='https://www.tiktok.com/@u/video/123', formats=[fmt])
    path = tmp_path / 'original.mp4'
    path.write_bytes(b'original')
    menu_app.service.analyze.return_value = info
    menu_app.service.download.return_value = DownloadResult(video_info=info, file_path=path,
        file_size_bytes=8, format=fmt, verified_format=fmt)
    await feed_message(menu_app, str(info.webpage_url))
    assert sum(isinstance(c, SendMessage) for c in menu_app.calls) == 1
    edits = [c for c in menu_app.calls if isinstance(c, EditMessageText)]
    assert {c.message_id for c in edits} == {20}
    assert any('Descarc' in c.text for c in edits) and any('trimiterea' in c.text for c in edits)
    assert edits[-1].text.startswith('✅')
    assert sum(isinstance(c, SendVideo) for c in menu_app.calls) == 1
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['📥 Descarcă video', '📥 Descarcă TikTok', '❓ Cum folosesc botul', 'ℹ️ Limite și calitate', '/menu'])
async def test_menu_buttons_are_routed_before_link_analysis(menu_app, text):
    from aiogram.methods import SendMessage
    await feed_message(menu_app, text)
    menu_app.service.analyze.assert_not_awaited()
    messages = [c for c in menu_app.calls if isinstance(c, SendMessage)]
    assert messages and 'link TikTok valid' not in messages[-1].text
    assert messages[-1].reply_markup is not None


@pytest.mark.asyncio
async def test_automatic_video_uses_fixed_source_policy(menu_app, tmp_path):
    from aiogram.methods import SendVideo
    from bot.models import VideoInfo, VideoFormat, DownloadResult
    source = VideoFormat(format_id='clean', ext='mp4', width=720, height=1280, has_watermark=False,
                         video_codec='h264', audio_codec='aac', has_audio=True)
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@author/video/123', formats=[source])
    path = tmp_path / 'original.mp4'
    path.write_bytes(b'original')
    menu_app.service.analyze.return_value = info
    menu_app.service.download.return_value = DownloadResult(video_info=info, format=source,
        verified_format=source, file_path=path, file_size_bytes=8)
    await feed_message(menu_app, str(info.webpage_url))
    menu_app.service.download.assert_awaited_once()
    assert menu_app.service.download.call_args.kwargs == {}
    assert any(isinstance(c, SendVideo) for c in menu_app.calls)
    assert not path.exists()


@pytest.mark.asyncio
async def test_cancel_choice_is_owner_bound_and_does_not_interrupt_active_download():
    from bot.handlers.callbacks import cancel_handler
    from bot.handlers.keyboards import CancelCallback, download_keyboard
    from bot.models import VideoInfo, VideoFormat
    from bot.services.telegram_download import PendingRequests
    pending = PendingRequests()
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@author/video/123',
                     formats=[VideoFormat(has_watermark=False, format_id='max', ext='mp4')])
    item = pending.add(11, 10, 20, info)
    keyboard = download_keyboard(item.key, info)
    assert keyboard.inline_keyboard[-1][0].callback_data.startswith('cancel:')
    message = SimpleNamespace(message_id=20, chat=SimpleNamespace(id=10), edit_text=AsyncMock())
    query = SimpleNamespace(data=CancelCallback(key=item.key).pack(), from_user=SimpleNamespace(id=22),
                            message=message, answer=AsyncMock())
    await cancel_handler(query, pending)
    assert pending.get(item.key) is item
    query.from_user.id = 11
    item.busy = True
    await cancel_handler(query, pending)
    assert pending.get(item.key) is item
    item.busy = False
    await cancel_handler(query, pending)
    assert pending.get(item.key) is None
    assert message.edit_text.call_args.kwargs['reply_markup'] is None


@pytest.mark.asyncio
async def test_oversized_highest_source_asks_instead_of_downloading(menu_app):
    from aiogram.methods import EditMessageText
    from bot.models import VideoInfo, VideoFormat
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@author/video/123', formats=[
        VideoFormat(has_watermark=False, format_id='max', ext='mp4', width=2160, height=3840, filesize=80_000_000),
        VideoFormat(format_id='clean', ext='mp4', has_watermark=False, filesize=20_000_000)])
    menu_app.service.analyze.return_value = info
    await feed_message(menu_app, str(info.webpage_url))
    menu_app.service.download.assert_not_awaited()
    edits = [c for c in menu_app.calls if isinstance(c, EditMessageText)]
    assert edits[-1].reply_markup is None
    assert 'prea mare' in edits[-1].text


@pytest.mark.asyncio
async def test_cancel_command_only_removes_own_idle_requests(menu_app):
    from bot.models import VideoInfo, VideoFormat
    pending = menu_app.dispatcher['pending']
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@author/video/123',
                     formats=[VideoFormat(has_watermark=False, format_id='max', ext='mp4')])
    own = pending.add(11, 10, 20, info)
    other = pending.add(22, 10, 21, info)
    active = pending.add(11, 10, 22, info)
    active.busy = True
    await feed_message(menu_app, '/cancel')
    assert pending.get(own.key) is None
    assert pending.get(other.key) is other
    assert pending.get(active.key) is active


@pytest.mark.asyncio
async def test_photos_keep_delivery_choice_before_downloading(menu_app):
    from bot.models import VideoInfo, PhotoInfo
    menu_app.service.analyze.return_value = VideoInfo(id='123', media_type='photo',
        webpage_url='https://www.tiktok.com/@author/photo/123', photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/photo.jpg'])])
    await feed_message(menu_app, 'https://www.tiktok.com/@author/video/123')
    menu_app.service.download.assert_not_awaited()
