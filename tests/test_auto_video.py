from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import SendVideo, SendDocument, EditMessageReplyMarkup, EditMessageCaption
from aiogram.types import Update

from bot.models import VideoInfo, VideoFormat, DownloadResult
from tests.test_menu import menu_app, feed_message
from tests.test_audio import media_fixture


@pytest.mark.asyncio
async def test_incompatible_original_becomes_playable_preview_without_changing_original(tmp_path):
    import shutil
    import subprocess
    from bot.services.media import verify_media
    from bot.services.telegram_sender import TelegramSender
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg required for local preview integration')
    source = tmp_path / 'original.mkv'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=black:s=64x64:d=1', '-c:v', 'mpeg4', str(source)], check=True)
    original_bytes = source.read_bytes()
    verified = await verify_media(source)
    info = VideoInfo(id='123', title='Titlu', webpage_url='https://www.tiktok.com/@u/video/123')
    result = DownloadResult(video_info=info, file_path=source, file_size_bytes=len(original_bytes),
                             format=verified, verified_format=verified)
    async def video(**kwargs):
        assert kwargs['supports_streaming'] is True
        preview = await verify_media(kwargs['video'].path)
        assert preview.video_codec == 'h264' and preview.ext == 'mp4'
        return SimpleNamespace(video=SimpleNamespace(file_id='preview-id'))
    bot = SimpleNamespace(send_video=AsyncMock(side_effect=video), send_document=AsyncMock())
    sender = TelegramSender()
    receipt = await sender.send_with_receipt(bot, 10, result, 'preview')
    assert receipt.kind == 'video' and receipt.verified_format.transcoded is True
    assert receipt.file_size_bytes > 0
    assert source.read_bytes() == original_bytes
    assert list(tmp_path.iterdir()) == [source]
    bot.send_document.assert_not_awaited()
    await sender.send_with_receipt(bot, 10, result, 'document')
    assert bot.send_document.call_args.kwargs['document'].path == source
    assert source.read_bytes() == original_bytes


@pytest.mark.asyncio
async def test_preview_conversion_size_limit_removes_partial(tmp_path, media_fixture):
    from bot.services.preview import create_preview
    from bot.models.exceptions import FileSizeLimitError
    output = tmp_path / 'preview.mp4'
    with pytest.raises(FileSizeLimitError):
        await create_preview(media_fixture, output, max_bytes=100, timeout=10)
    assert not output.exists() and media_fixture.exists()


@pytest.mark.asyncio
async def test_preview_conversion_cancellation_removes_partial(tmp_path, monkeypatch, media_fixture):
    import asyncio
    from bot.services import preview
    output = tmp_path / 'preview.mp4'
    started = asyncio.Event()
    async def stalled(*args, **kwargs):
        output.write_bytes(b'partial')
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(preview, 'require_media_tools', lambda: None)
    monkeypatch.setattr(preview, 'run_process', stalled)
    task = asyncio.create_task(preview.create_preview(media_fixture, output, max_bytes=100, timeout=10))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not output.exists()


@pytest.mark.asyncio
async def test_video_arrives_automatically_then_file_on_demand(menu_app, tmp_path):
    fmt = VideoFormat(has_watermark=False, format_id='play', ext='mp4', width=720, height=1280,
                      video_codec='h264', audio_codec='aac', has_audio=True)
    info = VideoInfo(id='123', title='Titlu', webpage_url='https://www.tiktok.com/@u/video/123', formats=[fmt])
    menu_app.service.analyze.return_value = info
    paths = []
    async def download(url, **kwargs):
        path = tmp_path / f'original-{len(paths)}.mp4'
        path.write_bytes(b'original-bytes')
        paths.append(path)
        return DownloadResult(video_info=info, file_path=path, file_size_bytes=14, format=fmt, verified_format=fmt)
    menu_app.service.download.side_effect = download
    await feed_message(menu_app, str(info.webpage_url))
    assert sum(isinstance(c, SendVideo) for c in menu_app.calls) == 1
    assert not any(isinstance(c, SendDocument) for c in menu_app.calls)
    controls = next(c for c in reversed(menu_app.calls) if isinstance(c, EditMessageReplyMarkup))
    row = controls.reply_markup.inline_keyboard[0]
    assert [b.text for b in row] == ['📁 Ca fișier', 'ℹ️ Info']
    assert row[0].callback_data.startswith('va:file:') and row[1].callback_data == 'mi:show'
    message = {'message_id': controls.message_id, 'date': 1, 'chat': {'id': 10, 'type': 'private'},
               'caption': '<b>Titlu</b>', 'reply_markup': controls.reply_markup.model_dump()}
    await menu_app.dispatcher.feed_update(menu_app.bot, Update.model_validate({'update_id': 2, 'callback_query': {
        'id': 'info', 'from': {'id': 11, 'is_bot': False, 'first_name': 'Test'},
        'chat_instance': 'test', 'data': 'mi:show', 'message': message,
    }}), download_service=menu_app.service)
    expanded = next(c for c in reversed(menu_app.calls) if isinstance(c, EditMessageCaption))
    assert expanded.reply_markup.inline_keyboard[0][0].callback_data == row[0].callback_data
    assert expanded.reply_markup.inline_keyboard[0][1].callback_data == 'mi:hide'
    await menu_app.dispatcher['preferences'].update_preferences(11, quality='low')
    await menu_app.dispatcher.feed_update(menu_app.bot, Update.model_validate({'update_id': 3, 'callback_query': {
        'id': 'file', 'from': {'id': 11, 'is_bot': False, 'first_name': 'Test'},
        'chat_instance': 'test', 'data': row[0].callback_data, 'message': message,
    }}), download_service=menu_app.service)
    assert sum(isinstance(c, SendVideo) for c in menu_app.calls) == 1
    assert sum(isinstance(c, SendDocument) for c in menu_app.calls) == 1
    assert menu_app.service.download.call_args.kwargs['quality'] == 'high'
    assert not any(path.exists() for path in paths)


@pytest.mark.asyncio
async def test_failed_automatic_preview_retries_as_video_with_file_controls(menu_app, tmp_path, media_fixture):
    import shutil
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import EditMessageText
    from bot.services.media import verify_media
    verified = await verify_media(media_fixture)
    info = VideoInfo(id='123', title='Retry', webpage_url='https://www.tiktok.com/@u/video/123', formats=[verified.model_copy(update={'has_watermark': False})])
    menu_app.service.analyze.return_value = info
    attempt = 0
    async def download(url, **kwargs):
        nonlocal attempt
        path = tmp_path / f'retry-{attempt}.mp4'
        attempt += 1
        shutil.copyfile(media_fixture, path)
        return DownloadResult(video_info=info, file_path=path, file_size_bytes=path.stat().st_size,
                                format=verified, verified_format=await verify_media(path))
    menu_app.service.download.side_effect = download
    request = menu_app.bot.session.side_effect
    fail = True
    async def fail_once(client, method, timeout=None):
        nonlocal fail
        if isinstance(method, SendVideo) and fail:
            fail = False
            raise TelegramNetworkError(method=method, message='temporary upload failure')
        return await request(client, method, timeout)
    menu_app.bot.session.side_effect = fail_once
    await feed_message(menu_app, str(info.webpage_url))
    retry = next(c for c in reversed(menu_app.calls) if isinstance(c, EditMessageText))
    data = retry.reply_markup.inline_keyboard[0][0].callback_data
    await menu_app.dispatcher.feed_update(menu_app.bot, Update.model_validate({'update_id': 2, 'callback_query': {
        'id': 'retry', 'from': {'id': 11, 'is_bot': False, 'first_name': 'Test'},
        'chat_instance': 'test', 'data': data,
        'message': {'message_id': 20, 'date': 1, 'chat': {'id': 10, 'type': 'private'}, 'text': retry.text},
    }}), download_service=menu_app.service)
    assert sum(isinstance(c, SendVideo) for c in menu_app.calls) == 1
    assert not any(isinstance(c, SendDocument) for c in menu_app.calls)
    controls = next(c for c in reversed(menu_app.calls) if isinstance(c, EditMessageReplyMarkup))
    assert controls.reply_markup.inline_keyboard[0][0].callback_data.startswith('va:file:')


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['owner', 'chat', 'message', 'busy', 'expired'])
async def test_delivered_file_button_is_bound_to_owner_and_message(invalid):
    from bot.handlers.callbacks import video_action_handler
    from bot.services.telegram_download import PendingRequests
    from bot.services.telegram_sender import TelegramSender
    pending = PendingRequests()
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@u/video/123')
    item = pending.add(11, 10, 30, info)
    if invalid == 'expired':
        pending.remove(item.key)
    item.busy = invalid == 'busy'
    message = SimpleNamespace(message_id=99 if invalid == 'message' else 30,
        chat=SimpleNamespace(id=99 if invalid == 'chat' else 10), answer=AsyncMock())
    query = SimpleNamespace(data=f'va:file:{item.key}', message=message,
        from_user=SimpleNamespace(id=99 if invalid == 'owner' else 11), answer=AsyncMock())
    service = SimpleNamespace(download=AsyncMock())
    await video_action_handler(query, SimpleNamespace(), service, pending, TelegramSender())
    message.answer.assert_not_awaited()
    service.download.assert_not_awaited()
    assert query.answer.call_args.kwargs['show_alert']
