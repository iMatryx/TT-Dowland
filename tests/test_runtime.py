import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.config import Settings
from bot.models import DownloadResult, VideoFormat, VideoInfo
from bot.services.runtime import BotRuntime
from bot.services.telegram_sender import TelegramSender


@pytest.fixture
async def setup_runtime(tmp_path: Path):
    settings = Settings(DOWNLOAD_DIR=tmp_path / "downloads", CACHE_DB_PATH=tmp_path / "cache.sqlite3",
                        SHUTDOWN_GRACE_SECONDS=0.02)
    runtime = BotRuntime(settings)
    await runtime.start()
    source = VideoFormat(has_watermark=False, format_id="source", ext="mp4", width=1080, height=1920,
                         fps=30, video_codec="h264", audio_codec="aac", has_audio=True)
    info = VideoInfo(id="123", webpage_url="https://www.tiktok.com/@author/video/123", formats=[source])
    created = []

    async def download(url, *, preference="prefer_clean"):
        path = runtime.work_dir / f"tiktok-{len(created)}.mp4"
        path.write_bytes(b"original")
        created.append(path)
        return DownloadResult(video_info=info, format=source, verified_format=source,
                              file_path=path, file_size_bytes=8)

    service = SimpleNamespace(download=AsyncMock(side_effect=download))
    bot = SimpleNamespace(
        id=12345,
        send_document=AsyncMock(return_value=SimpleNamespace(document=SimpleNamespace(file_id="document-id"))),
        send_video=AsyncMock(return_value=SimpleNamespace(video=SimpleNamespace(file_id="video-id"))),
    )
    state = SimpleNamespace(runtime=runtime, bot=bot, service=service, info=info, sender=TelegramSender(),
                            progress=SimpleNamespace(update=AsyncMock()), created=created)
    try:
        yield state
    finally:
        await runtime.close()


async def deliver(state, chat, mode="document"):
    await state.runtime.deliver(state.bot, chat, state.info, mode, state.service, state.sender, state.progress)


@pytest.mark.asyncio
async def test_audio_cache_is_reused_without_satisfying_video_requests(setup_runtime):
    state = setup_runtime
    original_download = state.service.download.side_effect
    async def download(url, *, audio_only=False, quality='high', **kwargs):
        if not audio_only:
            return await original_download(url, **kwargs)
        path = state.runtime.work_dir / 'sound.mp3'
        path.write_bytes(b'audio')
        state.created.append(path)
        return DownloadResult(video_info=state.info, file_path=path, file_size_bytes=5, audio_only=True,
                              format=state.info.formats[0], verified_format=VideoFormat(has_watermark=False,
                                  format_id='probe', ext='mp3', has_video=False, has_audio=True, audio_codec='mp3'))
    state.service.download.side_effect = download
    state.bot.send_audio = AsyncMock(return_value=SimpleNamespace(audio=SimpleNamespace(file_id='audio-id')))
    await deliver(state, 1, 'audio')
    await deliver(state, 2, 'audio')
    assert state.service.download.await_count == 1
    assert state.bot.send_audio.call_args.kwargs['audio'] == 'audio-id'
    await deliver(state, 3, 'document')
    assert state.service.download.await_count == 2
    assert state.bot.send_document.call_args.kwargs['document'] != 'audio-id'
    assert not any(path.exists() for path in state.created)


def test_cache_identity_includes_source_platform():
    from bot.services.runtime import quality_key
    source = VideoFormat(has_watermark=False, format_id='same', ext='mp4', has_audio=True)
    first = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@u/video/123', formats=[source])
    second = first.model_copy(update={'webpage_url': 'https://www.instagram.com/reel/123/'})
    assert quality_key(first) != quality_key(second)


@pytest.mark.asyncio
async def test_automatic_video_cache_hit_binds_new_controls_to_recipient(setup_runtime):
    from datetime import datetime, timezone
    from aiogram.types import Message, Chat, Video
    from bot.services.telegram_download import PendingRequests
    state = setup_runtime
    pending = PendingRequests()
    sender = TelegramSender(details=state.runtime.preferences, pending=pending)
    async def video(**kwargs):
        return Message(message_id=30 + kwargs['chat_id'], date=datetime.now(timezone.utc),
            chat=Chat(id=kwargs['chat_id'], type='private'),
            video=Video(file_id='preview-id', file_unique_id='preview-unique', width=1080, height=1920, duration=1))
    state.bot.send_video.side_effect = video
    state.bot.edit_message_reply_markup = AsyncMock()
    for chat, owner in [(1, 11), (2, 22)]:
        await state.runtime.deliver(state.bot, chat, state.info, 'preview', state.service, sender, state.progress, owner_id=owner)
        controls = state.bot.edit_message_reply_markup.call_args.kwargs
        key = controls['reply_markup'].inline_keyboard[0][0].callback_data.rsplit(':', 1)[1]
        item = pending.get(key)
        assert (item.user_id, item.chat_id, item.message_id) == (owner, chat, 30 + chat)
    state.service.download.assert_awaited_once()
    assert state.bot.send_video.call_args.kwargs['video'] == 'preview-id'


@pytest.mark.asyncio
async def test_successful_cache_hit_reuses_document_file_id(setup_runtime) -> None:
    state = setup_runtime
    await deliver(state, 1)
    await deliver(state, 2)
    state.service.download.assert_awaited_once()
    assert state.bot.send_document.call_args.kwargs["document"] == "document-id"
    assert not any(path.exists() for path in state.created)


@pytest.mark.asyncio
async def test_video_cache_cannot_satisfy_original_request(setup_runtime) -> None:
    state = setup_runtime
    await deliver(state, 1, "video")
    await deliver(state, 2, "document")
    assert state.service.download.await_count == 2
    assert state.bot.send_document.call_args.kwargs["document"] != "video-id"


@pytest.mark.asyncio
async def test_identical_simultaneous_requests_download_once(setup_runtime) -> None:
    state = setup_runtime
    await asyncio.gather(deliver(state, 1), deliver(state, 2), deliver(state, 3))
    state.service.download.assert_awaited_once()
    assert state.bot.send_document.await_count == 3
    assert {call.kwargs["chat_id"] for call in state.bot.send_document.call_args_list} == {1, 2, 3}
    assert not any(path.exists() for path in state.created)


@pytest.mark.asyncio
async def test_video_and_document_share_file_without_early_cleanup(setup_runtime) -> None:
    state = setup_runtime
    video_ready, document_ready = asyncio.Event(), asyncio.Event()

    async def video(**kwargs):
        video_ready.set()
        await document_ready.wait()
        assert state.created[0].exists()
        return SimpleNamespace(video=SimpleNamespace(file_id="video-id"))

    async def document(**kwargs):
        document_ready.set()
        await video_ready.wait()
        assert state.created[0].exists()
        return SimpleNamespace(document=SimpleNamespace(file_id="document-id"))

    state.bot.send_video.side_effect = video
    state.bot.send_document.side_effect = document
    await asyncio.wait_for(asyncio.gather(deliver(state, 1, "video"), deliver(state, 2, "document")), 2)
    state.service.download.assert_awaited_once()
    assert not state.created[0].exists()


@pytest.mark.asyncio
async def test_shutdown_cancels_upload_and_cleans_artifact(setup_runtime) -> None:
    state = setup_runtime
    sending = asyncio.Event()

    async def stalled(**kwargs):
        sending.set()
        await asyncio.Event().wait()

    state.bot.send_document.side_effect = stalled
    task = asyncio.create_task(deliver(state, 1))
    state.runtime.handlers.add(task)
    await asyncio.wait_for(sending.wait(), 2)
    await state.runtime.close()
    assert task.cancelled()
    assert state.runtime.client is None
    assert not state.created[0].exists()


@pytest.mark.asyncio
async def test_restart_recovers_abandoned_files_but_preserves_cli_files(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    work = downloads / "bot-work"
    work.mkdir(parents=True)
    stale = work / ".tiktok-abandoned"
    stale.mkdir()
    (stale / "partial").write_bytes(b"part")
    cli_file = downloads / "tiktok-cli.mp4"
    cli_file.write_bytes(b"original")
    runtime = BotRuntime(Settings(DOWNLOAD_DIR=downloads, CACHE_ENABLED=False))
    try:
        await runtime.start()
        assert not stale.exists()
        assert cli_file.read_bytes() == b"original"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_failed_destination_does_not_fail_healthy_recipient(setup_runtime) -> None:
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendDocument
    from bot.models.exceptions import TelegramDeliveryError

    state = setup_runtime
    joined, sending = asyncio.Event(), asyncio.Event()
    original_run = state.runtime.delivery_queue.run
    admissions = 0

    async def run(key, operation):
        nonlocal admissions
        admissions += 1
        if admissions == 2:
            joined.set()
        return await original_run(key, operation)

    state.runtime.delivery_queue.run = run

    async def send(**kwargs):
        if kwargs["chat_id"] == 1:
            sending.set()
            await joined.wait()
            raise TelegramForbiddenError(method=SendDocument(chat_id=1, document="file"), message="blocked")
        return SimpleNamespace(document=SimpleNamespace(file_id="healthy-id"))

    state.bot.send_document.side_effect = send
    first = asyncio.create_task(deliver(state, 1))
    await asyncio.wait_for(sending.wait(), 1)
    second = asyncio.create_task(deliver(state, 2))
    results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 2)
    assert isinstance(results[0], TelegramDeliveryError)
    assert results[1] is None
    assert 2 in {call.kwargs["chat_id"] for call in state.bot.send_document.call_args_list}


@pytest.mark.asyncio
async def test_invalid_file_id_is_evicted_and_reuploaded(setup_runtime) -> None:
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendDocument

    state = setup_runtime
    await deliver(state, 1)

    async def send(**kwargs):
        if kwargs["document"] == "document-id":
            raise TelegramBadRequest(method=SendDocument(chat_id=2, document=kwargs["document"]),
                                     message="wrong file identifier")
        return SimpleNamespace(document=SimpleNamespace(file_id="replacement-id"))

    state.bot.send_document.side_effect = send
    await deliver(state, 2)
    assert state.service.download.await_count == 2
    await deliver(state, 3)
    assert state.service.download.await_count == 2
    assert state.bot.send_document.call_args.kwargs["document"] == "replacement-id"
    assert not any(path.exists() for path in state.created)


@pytest.mark.asyncio
async def test_improved_reported_bitrate_is_not_served_from_lower_quality_cache(setup_runtime) -> None:
    state = setup_runtime
    await deliver(state, 1)
    state.info.formats[0].tbr = 6000
    await deliver(state, 2)
    assert state.service.download.await_count == 2


@pytest.mark.asyncio
async def test_cancelling_one_destination_does_not_upload_to_it_or_cancel_follower(setup_runtime) -> None:
    state = setup_runtime
    started, release, both = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = state.service.download.side_effect
    status_count = 0

    async def download(url, *, preference="prefer_clean"):
        started.set()
        await release.wait()
        return await original(url, preference=preference)

    async def status(text):
        nonlocal status_count
        if text == "📥 Descarc videoclipul...":
            status_count += 1
            if status_count == 2:
                both.set()

    state.service.download.side_effect = download
    state.progress.update.side_effect = status
    first = asyncio.create_task(deliver(state, 1))
    await started.wait()
    second = asyncio.create_task(deliver(state, 2))
    try:
        await asyncio.wait_for(both.wait(), 1)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        await asyncio.wait_for(second, 1)
        state.service.download.assert_awaited_once()
        assert [call.kwargs["chat_id"] for call in state.bot.send_document.call_args_list] == [2]
        assert not state.created[0].exists()
    finally:
        release.set()
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_clean_document_preference_uses_clean_selection_and_original_cache(setup_runtime):
    state = setup_runtime
    state.info.formats[0].has_watermark = False
    state.info.formats.append(VideoFormat(format_id='higher-watermarked', ext='mp4',
        width=2160, height=3840, has_watermark=True, has_audio=True))
    await deliver(state, 1, 'clean_document')
    assert state.service.download.call_args.kwargs['preference'] == 'clean'
    state.bot.send_video.assert_not_awaited()
    await deliver(state, 2, 'clean_document')
    state.service.download.assert_awaited_once()
    assert state.bot.send_document.call_args.kwargs['document'] == 'document-id'
    await deliver(state, 3, 'document')
    state.service.download.assert_awaited_once()
    assert state.service.download.call_args.kwargs['preference'] == 'clean'


async def test_telegram_cache_counts_logical_lookups_and_uploads(setup_runtime, caplog):
    import json
    import logging
    caplog.set_level(logging.INFO, logger='bot.performance')
    state = setup_runtime
    await deliver(state, 1)
    await deliver(state, 2)
    events = [json.loads(r.message) for r in caplog.records if r.name == 'bot.performance']
    lookups = [e for e in events if e.get('cache') == 'telegram']
    assert [e['outcome'] for e in lookups] == ['miss', 'hit']
    assert lookups[-1]['hit_rate'] == 0.5
    uploads = [e for e in events if e.get('stage') == 'upload']
    assert len(uploads) == 2
    assert all(e['platform'] == 'tiktok' and e['result'] == 'success' for e in uploads)
    assert 'document-id' not in json.dumps(events)
