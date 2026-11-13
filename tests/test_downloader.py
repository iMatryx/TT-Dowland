import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.config import Settings
from bot.models import VideoFormat, VideoInfo
from bot.models.exceptions import (
    CorruptFileError, DownloadTimeoutError, FileSizeLimitError, InsufficientSpaceError,
)
from bot.services import downloader
from bot.services.extraction import Extraction

URL = "https://www.tiktok.com/@author/video/123"


class FakeExtractor:
    def __init__(self, *, size: int = 8) -> None:
        self.size = size

    async def extract(self, url: str, workspace: Path) -> Extraction:
        info_path = workspace / "info.json"
        info_path.write_text("{}", encoding="utf-8")
        return Extraction(VideoInfo(
            id="123", uploader="author", webpage_url=url,
            formats=[VideoFormat(has_watermark=False, format_id="play", ext="mp4", width=1080,
                                 height=1920, filesize=self.size)],
        ), info_path)


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(downloader, "require_media_tools", lambda **kwargs: None)
    monkeypatch.setattr(downloader.shutil, "disk_usage", lambda path: SimpleNamespace(free=10**9))

    async def worker(action: str, payload: Path, timeout: float) -> None:
        data = json.loads(payload.read_text())
        (Path(data["media_dir"]) / "original.mp4").write_bytes(b"original")

    async def verify(path: Path, *, timeout: float, full_decode: bool = False) -> VideoFormat:
        return VideoFormat(has_watermark=False, format_id="ffprobe", ext="mp4", width=1080, height=1920, has_audio=True)

    monkeypatch.setattr(downloader, "run_worker", worker)
    monkeypatch.setattr(downloader, "verify_media", verify)
    return downloader.TikTokDownloader(
        Settings(DOWNLOAD_DIR=tmp_path, MAX_FILE_SIZE_MB=1), extractor=FakeExtractor(),
    )


@pytest.mark.asyncio
async def test_success_publishes_original_and_preserves_author(engine, tmp_path: Path) -> None:
    result = await engine.download(URL)
    assert result.file_path.read_bytes() == b"original"
    assert result.video_info.uploader == "author"
    assert result.verified_format.width == 1080
    assert result.format.has_watermark is False
    assert any("no watermark" in warning for warning in result.warnings)
    assert list(tmp_path.iterdir()) == [result.file_path]


@pytest.mark.asyncio
async def test_corruption_never_publishes_and_cleans(engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def reject(path: Path, *, timeout: float, full_decode: bool = False):
        raise CorruptFileError("bad media")

    monkeypatch.setattr(downloader, "verify_media", reject)
    with pytest.raises(CorruptFileError):
        await engine.download(URL)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_exact_oversize_is_rejected(engine, tmp_path: Path) -> None:
    engine.extractor = FakeExtractor(size=2 * 1024 * 1024)
    with pytest.raises(FileSizeLimitError):
        await engine.download(URL)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_insufficient_space_is_rejected(engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    with pytest.raises(InsufficientSpaceError):
        await engine.download(URL)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_timeout_cleans_workspace(engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine.timeout = 0.02

    async def stalled(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(downloader, "run_worker", stalled)
    with pytest.raises(DownloadTimeoutError):
        await engine.download(URL)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_cancellation_cleans_workspace(engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()

    async def stalled(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(downloader, "run_worker", stalled)
    task = asyncio.create_task(engine.download(URL))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_same_video_has_isolated_concurrent_workspaces(engine, tmp_path: Path) -> None:
    first, second = await asyncio.gather(engine.download(URL), engine.download(URL))
    assert first.file_path != second.file_path
    assert len(list(tmp_path.iterdir())) == 2


async def test_performance_stages_share_safe_operation_context(engine, caplog):
    import logging
    caplog.set_level(logging.INFO, logger='bot.performance')
    result = await engine.download(URL)
    try:
        events = [json.loads(r.message) for r in caplog.records if r.name == 'bot.performance']
        assert {'analysis', 'download', 'verification'} <= {e.get('stage') for e in events}
        assert len({e['operation_id'] for e in events}) == 1
        assert all(e['platform'] == 'tiktok' and e.get('result', 'success') == 'success' for e in events)
        assert 'author' not in json.dumps(events)
    finally:
        result.cleanup()


async def test_shared_delivery_measures_one_download_and_two_uploads(engine, tmp_path, caplog):
    import logging
    from unittest.mock import AsyncMock
    from bot.services.runtime import BotRuntime
    from bot.services.telegram_download import TelegramDownloadService
    from bot.services.telegram_sender import TelegramSender
    caplog.set_level(logging.INFO, logger='bot.performance')
    runtime = BotRuntime(Settings(DOWNLOAD_DIR=tmp_path, CACHE_DB_PATH=tmp_path / 'cache.db'))
    info = VideoInfo(id='123', webpage_url=URL, formats=[VideoFormat(has_watermark=False, format_id='play', ext='mp4')])
    bot = SimpleNamespace(id=12345, send_document=AsyncMock(
        return_value=SimpleNamespace(document=SimpleNamespace(file_id='private-file-id'))))
    service, sender = TelegramDownloadService(engine), TelegramSender()
    progress = SimpleNamespace(update=AsyncMock())
    try:
        await runtime.start()
        await asyncio.gather(*(runtime.deliver(bot, chat, info, 'document', service, sender, progress)
                               for chat in [10, 20]))
        events = [json.loads(r.message) for r in caplog.records if r.name == 'bot.performance']
        assert len([e for e in events if e.get('stage') == 'download']) == 1
        uploads = [e for e in events if e.get('stage') == 'upload']
        assert len(uploads) == 2
        assert len({e['operation_id'] for e in uploads}) == 2
        assert 'private-file-id' not in json.dumps(events) and 'author' not in json.dumps(events)
    finally:
        await runtime.close()


async def test_published_download_keeps_bound_verification(engine):
    from bot.services.media import verification_matches
    result = await engine.download(URL)
    try:
        assert verification_matches(result.file_path, result.verified_format)
    finally:
        result.cleanup()


@pytest.mark.parametrize('clean_available', [True, False])
async def test_download_enforces_fixed_policy_even_for_legacy_arguments(engine, monkeypatch, clean_available):
    from bot.models.exceptions import SourceVariantUnavailableError
    source_extract = engine.extractor.source.extract
    async def extract(url, workspace):
        extraction = await source_extract(url, workspace)
        best = extraction.video.formats[0]
        best.has_watermark = not clean_available
        extraction.video.formats.extend([
            best.model_copy(update={'format_id': 'marked-4k', 'width': 2160, 'height': 3840,
                                    'has_watermark': True}),
            best.model_copy(update={'format_id': 'smaller', 'width': 360, 'height': 640}),
        ])
        return extraction
    monkeypatch.setattr(engine.extractor.source, 'extract', extract)
    if not clean_available:
        with pytest.raises(SourceVariantUnavailableError):
            await engine.download(URL, quality='low', watermark='any', preference='maximum')
        assert not list(engine.settings.download_dir.iterdir())
    else:
        result = await engine.download(URL, quality='low', watermark='any', preference='maximum')
        try:
            assert result.format.format_id == 'play' and result.format.has_watermark is False
            assert result.format.width == 1080
        finally:
            result.cleanup()
