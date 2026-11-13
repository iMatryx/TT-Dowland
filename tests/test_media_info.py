from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.services.cache import SQLiteCache


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['owner', 'message', 'chat', 'expired', 'busy'])
async def test_pending_info_rejects_foreign_expired_and_busy_requests(invalid):
    from bot.handlers.media_info import pending_info_handler
    from bot.models import VideoInfo
    from bot.services.telegram_download import PendingRequests
    from bot.services.telegram_sender import TelegramSender
    pending = PendingRequests()
    item = pending.add(11, 10, 20, VideoInfo(id='123', webpage_url='https://www.tiktok.com/@u/video/123'))
    if invalid == 'expired':
        pending.remove(item.key)
    item.busy = invalid == 'busy'
    message = SimpleNamespace(message_id=99 if invalid == 'message' else 20,
        chat=SimpleNamespace(id=99 if invalid == 'chat' else 10), edit_text=AsyncMock())
    query = SimpleNamespace(data=f'pi:show:{item.key}', message=message,
        from_user=SimpleNamespace(id=99 if invalid == 'owner' else 11), answer=AsyncMock())
    await pending_info_handler(query, pending, TelegramSender(), SimpleNamespace(max_download_bytes=50_000_000))
    message.edit_text.assert_not_awaited()
    assert query.answer.call_args.kwargs['show_alert'] is True


def test_audio_caption_is_title_only_with_details_in_info():
    from bot.models import VideoInfo, VideoFormat
    from bot.services.media_info import audio_captions
    info = VideoInfo(id='123', title='Titlu <test>', uploader='Autor',
                     webpage_url='https://www.youtube.com/shorts/AbCd_123-xy')
    fmt = VideoFormat(has_watermark=False, format_id='probe', ext='mp3', has_audio=True, has_video=False,
                      audio_codec='mp3', tbr=192, duration_seconds=12)
    short, expanded = audio_captions(info, fmt, 12345)
    assert short == '<b>Titlu &lt;test&gt;</b>'
    assert '192' in expanded and '12' in expanded and 'MP3' in expanded and 'Autor' in expanded


@pytest.mark.parametrize('kind', ['video', 'photo'])
def test_selection_info_preserves_multiline_titles_without_breaking_html(kind):
    from bot.models import VideoInfo, VideoFormat, PhotoInfo
    from bot.services.media_info import selection_captions
    info = VideoInfo(id='123', title='First\nSecond <test>', media_type=kind,
        webpage_url='https://www.tiktok.com/@u/video/123',
        formats=[VideoFormat(has_watermark=False, format_id='play', ext='mp4', width=720, height=1280)] if kind == 'video' else [],
        photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/photo.jpg'], width=720, height=1280)] if kind == 'photo' else [])
    short, expanded = selection_captions(info)
    assert short == '<b>First\nSecond &lt;test&gt;</b>'
    assert expanded.count('Second') == 1
    assert expanded.count('<b>') == expanded.count('</b>') == 1
    assert '720' in expanded


@pytest.mark.asyncio
async def test_media_details_persist_and_are_bound_to_the_target_message(tmp_path):
    path = tmp_path / 'cache.sqlite3'
    store = SQLiteCache(path)
    await store.put_media_details(123, 10, 20, 'Short', 'Expanded')
    await store.close()
    reopened = SQLiteCache(path)
    try:
        assert await reopened.get_media_details(123, 10, 20) == ('Short', 'Expanded')
        assert await reopened.get_media_details(999, 10, 20) is None
        assert await reopened.get_media_details(123, 99, 20) is None
        assert await reopened.get_media_details(123, 10, 99) is None
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_show_hide_edits_the_same_media_caption_without_resending(tmp_path):
    from bot.handlers.media_info import media_info_handler
    store = SQLiteCache(tmp_path / 'cache.sqlite3')
    await store.put_media_details(123, 10, 20, 'Short', 'Expanded 1080×1920')
    message = SimpleNamespace(message_id=20, chat=SimpleNamespace(id=10), edit_caption=AsyncMock())
    query = SimpleNamespace(data='mi:show', message=message, answer=AsyncMock())
    try:
        await media_info_handler(query, SimpleNamespace(id=123), store)
        assert message.edit_caption.call_args.kwargs['caption'] == 'Expanded 1080×1920'
        keyboard = message.edit_caption.call_args.kwargs['reply_markup']
        assert keyboard.inline_keyboard[0][0].callback_data == 'mi:hide'
        query.data = 'mi:hide'
        await media_info_handler(query, SimpleNamespace(id=123), store)
        assert message.edit_caption.call_args.kwargs['caption'] == 'Short'
        assert message.edit_caption.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'mi:show'
        assert message.edit_caption.await_count == 2
    finally:
        await store.close()


def test_video_info_contains_verified_parameters_and_safe_bounded_html():
    from bot.services.media_info import video_captions
    from bot.models import VideoInfo, VideoFormat
    source = VideoFormat(format_id='source', ext='mp4', width=720, height=1280, has_watermark=False)
    verified = VideoFormat(has_watermark=False, format_id='probe', ext='mp4', width=1080, height=1920, fps=30,
        tbr=4200, duration_seconds=12.5, video_codec='h264', audio_codec='aac', has_audio=True)
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@author/video/123', title='<tag>' * 1000)
    short, expanded = video_captions(info, verified, source, 12_345_678)
    assert '1080 × 1920' in expanded and 'verificată' in expanded
    assert '12.35 MB' in expanded and '30' in expanded and '4200' in expanded
    assert '12.5' in expanded and 'h264' in expanded and 'aac' in expanded
    assert 'neverificat vizual' in expanded
    assert '<tag>' not in expanded and '&lt;tag&gt;' in expanded
    assert len(short) <= 1024 and len(expanded) <= 1024
    assert 'FPS' not in short


@pytest.mark.asyncio
async def test_uploaded_and_cached_video_both_have_info_buttons(tmp_path):
    from datetime import datetime, timezone
    from aiogram.types import Message, Chat
    from bot.models import VideoInfo, VideoFormat, DownloadResult
    from bot.services.telegram_sender import TelegramSender
    from bot.services.cache import CacheEntry

    store = SQLiteCache(tmp_path / 'db.sqlite3')
    info = VideoInfo(id='123', title='Title', webpage_url='https://www.tiktok.com/@author/video/123')
    fmt = VideoFormat(has_watermark=False, format_id='probe', ext='mp4', width=1080, height=1920, video_codec='h264', has_audio=False)
    path = tmp_path / 'original.mp4'
    path.write_bytes(b'original')
    result = DownloadResult(video_info=info, format=fmt, verified_format=fmt, file_path=path, file_size_bytes=8)
    responses = [Message(message_id=i, date=datetime.now(timezone.utc), chat=Chat(id=10, type='private')) for i in [20, 21]]
    bot = SimpleNamespace(id=123, send_video=AsyncMock(side_effect=responses))
    try:
        sender = TelegramSender(details=store)
        await sender.send(bot, 10, result, 'video')
        entry = CacheEntry(bot_id=123, video_id='123', url=str(info.webpage_url), quality='q',
            kind='video', file_id='cached', size=8, source=fmt, verified=fmt)
        await sender.send_cached(bot, 10, entry, info)
        for call in bot.send_video.call_args_list:
            assert call.kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'mi:show'
            assert 'Rezoluție' not in call.kwargs['caption']
        assert '1080 × 1920' in (await store.get_media_details(123, 10, 20))[1]
        assert '1080 × 1920' in (await store.get_media_details(123, 10, 21))[1]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_album_info_controls_are_attached_to_each_photo_message(tmp_path):
    from datetime import datetime, timezone
    from aiogram.types import Message, Chat
    from bot.models import VideoInfo, PhotoInfo, PhotoFile, DownloadResult
    from bot.services.telegram_sender import TelegramSender

    store = SQLiteCache(tmp_path / 'db.sqlite3')
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@author/photo/123', media_type='photo',
        photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/1']), PhotoInfo(has_watermark=False, urls=['https://cdn.example/2'])])
    photos = []
    for i in range(2):
        path = tmp_path / f'{i}.jpg'
        path.write_bytes(b'image')
        photos.append(PhotoFile(file_path=path, width=1000 + i, height=1500, size=5))
    archive = tmp_path / 'originals.zip'
    archive.write_bytes(b'zip')
    result = DownloadResult(video_info=info, file_path=archive, file_size_bytes=3, photos=photos)
    responses = [Message(message_id=i, date=datetime.now(timezone.utc), chat=Chat(id=10, type='private')) for i in [20, 21]]
    bot = SimpleNamespace(id=123, send_media_group=AsyncMock(return_value=responses), edit_message_reply_markup=AsyncMock())
    try:
        await TelegramSender(details=store).send(bot, 10, result, 'album')
        assert 'reply_markup' not in bot.send_media_group.call_args.kwargs
        assert [c.kwargs['message_id'] for c in bot.edit_message_reply_markup.call_args_list] == [20, 21]
        details = (await store.get_media_details(123, 10, 21))[1]
        assert '1001 × 1500' in details and 'originală verificată' in details
        assert 'poate fi comprimat' in details and '2/2' in details
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_expired_or_missing_info_never_edits_another_message(tmp_path):
    from bot.handlers.media_info import media_info_handler
    store = SQLiteCache(tmp_path / 'db.sqlite3', ttl=60, capacity=1)
    try:
        await store.put_media_details(123, 10, 20, 'old', 'old details')
        await store.put_media_details(123, 10, 21, 'new', 'new details')
        assert await store.get_media_details(123, 10, 20) is None
        message = SimpleNamespace(message_id=20, chat=SimpleNamespace(id=10), edit_caption=AsyncMock())
        query = SimpleNamespace(data='mi:show', message=message, answer=AsyncMock())
        await media_info_handler(query, SimpleNamespace(id=123), store)
        message.edit_caption.assert_not_awaited()
        assert query.answer.call_args.kwargs['show_alert']
        await store._call(lambda db: db.execute('UPDATE media_details SET processed_at=0'))
        assert await store.get_media_details(123, 10, 21) is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_repeated_info_click_and_rejected_caption_edit_are_safe(tmp_path):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageCaption
    from bot.handlers.media_info import media_info_handler
    store = SQLiteCache(tmp_path / 'db.sqlite3')
    await store.put_media_details(123, 10, 20, 'short', 'expanded')
    message = SimpleNamespace(message_id=20, chat=SimpleNamespace(id=10), edit_caption=AsyncMock())
    query = SimpleNamespace(data='mi:show', message=message, answer=AsyncMock())
    try:
        message.edit_caption.side_effect = TelegramBadRequest(method=EditMessageCaption(chat_id=10, message_id=20),
            message='message is not modified')
        await media_info_handler(query, SimpleNamespace(id=123), store)
        assert not query.answer.call_args.kwargs.get('show_alert', False)
        message.edit_caption.side_effect = TelegramBadRequest(method=EditMessageCaption(chat_id=10, message_id=20),
            message='message cannot be edited')
        await media_info_handler(query, SimpleNamespace(id=123), store)
        assert query.answer.call_args.kwargs['show_alert']
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['album', 'photos', 'zip'])
async def test_single_photo_and_zip_offer_details_without_reencoding(tmp_path, mode):
    from datetime import datetime, timezone
    from aiogram.types import Message, Chat
    from bot.models import VideoInfo, PhotoInfo, PhotoFile, DownloadResult
    from bot.services.telegram_sender import TelegramSender
    store = SQLiteCache(tmp_path / 'db.sqlite3')
    photo = tmp_path / 'photo.jpg'
    photo.write_bytes(b'image')
    archive = tmp_path / 'originals.zip'
    archive.write_bytes(b'zip')
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@author/photo/123', media_type='photo',
        photos=[PhotoInfo(has_watermark=False, urls=['https://cdn.example/1'])])
    result = DownloadResult(video_info=info, file_path=archive, file_size_bytes=3,
        photos=[PhotoFile(file_path=photo, width=1000, height=1500, size=5)])
    response = Message(message_id=20, date=datetime.now(timezone.utc), chat=Chat(id=10, type='private'))
    bot = SimpleNamespace(id=123, send_document=AsyncMock(return_value=response), send_photo=AsyncMock(return_value=response))
    try:
        await TelegramSender(details=store).send(bot, 10, result, mode)
        call = bot.send_photo.call_args if mode == 'album' else bot.send_document.call_args
        assert call.kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'mi:show'
        details = await store.get_media_details(123, 10, 20)
        assert 'ZIP' in details[1] if mode == 'zip' else '1000 × 1500' in details[1]
        assert photo.read_bytes() == b'image' and archive.read_bytes() == b'zip'
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_metadata_error_after_upload_does_not_repeat_media(tmp_path):
    from datetime import datetime, timezone
    from aiogram.types import Message, Chat
    from bot.models import VideoInfo, VideoFormat, DownloadResult
    from bot.services.telegram_sender import TelegramSender
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@author/video/123')
    fmt = VideoFormat(has_watermark=False, format_id='probe', ext='mp4', width=1080, height=1920, video_codec='h264', has_audio=False)
    path = tmp_path / 'original.mp4'
    path.write_bytes(b'original')
    result = DownloadResult(video_info=info, format=fmt, verified_format=fmt, file_path=path, file_size_bytes=8)
    response = Message(message_id=20, date=datetime.now(timezone.utc), chat=Chat(id=10, type='private'))
    store = SimpleNamespace(put_media_details=AsyncMock(side_effect=OSError('database unavailable')))
    bot = SimpleNamespace(id=123, send_video=AsyncMock(return_value=response))
    assert await TelegramSender(details=store).send(bot, 10, result, 'video') == 'video'
    bot.send_video.assert_awaited_once()
