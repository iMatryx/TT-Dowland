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
    source = VideoFormat(format_id="source", ext="mp4", width=1080, height=1920,
                         fps=30, video_codec="h264", audio_codec="aac", has_audio=True)
    info = VideoInfo(id="123", webpage_url="https://www.tiktok.com/@author/video/123", formats=[source])
    created = []

    async def download(url):
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
    original_run = state.runtime.queue.run
    admissions = 0

    async def run(key, operation):
        nonlocal admissions
        admissions += 1
        if admissions == 2:
            joined.set()
        return await original_run(key, operation)

    state.runtime.queue.run = run

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

    async def download(url):
        started.set()
        await release.wait()
        return await original(url)

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
