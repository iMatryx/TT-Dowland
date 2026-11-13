from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from bot.config import Settings
from bot.models import VideoFormat, VideoInfo
from bot.models.exceptions import FileSizeLimitError, VideoUnavailableError
from bot.services.extraction import Extraction
from bot.services.telegram_download import TelegramDownloadService
from bot.utils.telegram_progress import ProgressMessage


@pytest.mark.asyncio
async def test_analysis_enforces_local_limit_and_cleans_workspace(tmp_path: Path) -> None:
    info = VideoInfo(id="123", webpage_url="https://www.tiktok.com/@author/video/123", formats=[
        VideoFormat(has_watermark=False, format_id="source", ext="mp4", filesize=2 * 1024 * 1024),
    ])
    engine = SimpleNamespace(
        settings=Settings(DOWNLOAD_DIR=tmp_path, MAX_FILE_SIZE_MB=1),
        extractor=SimpleNamespace(extract=AsyncMock(return_value=Extraction(info, tmp_path / "unused"))),
    )
    with pytest.raises(FileSizeLimitError):
        await TelegramDownloadService(engine).analyze(str(info.webpage_url))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_extraction_failure_cleans_analysis_workspace(tmp_path: Path) -> None:
    engine = SimpleNamespace(
        settings=Settings(DOWNLOAD_DIR=tmp_path),
        extractor=SimpleNamespace(extract=AsyncMock(side_effect=VideoUnavailableError("unavailable"))),
    )
    with pytest.raises(VideoUnavailableError):
        await TelegramDownloadService(engine).analyze("https://www.tiktok.com/@author/video/123")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_uneditable_progress_is_replaced() -> None:
    replacement = SimpleNamespace(message_id=99)
    old = SimpleNamespace(
        edit_text=AsyncMock(side_effect=TelegramBadRequest(
            method=EditMessageText(text="state", chat_id=10, message_id=20),
            message="message can't be edited",
        )),
        answer=AsyncMock(return_value=replacement),
    )
    progress = ProgressMessage(old)
    await progress.update("📥 Descarc videoclipul...")
    assert progress.message is replacement
    old.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_progress_does_not_duplicate_messages_for_other_bad_requests():
    message = SimpleNamespace(edit_text=AsyncMock(side_effect=TelegramBadRequest(
        method=EditMessageText(text='state', chat_id=10, message_id=20),
        message="can't parse entities")), answer=AsyncMock())
    with pytest.raises(TelegramBadRequest):
        await ProgressMessage(message).update('state')
    message.answer.assert_not_awaited()


@pytest.mark.parametrize('clean', [True, False])
async def test_analysis_limits_use_eligible_clean_video_and_independent_audio(tmp_path, clean):
    """An oversized marked video must not hide a smaller permitted delivery."""
    formats = [VideoFormat(format_id='marked', ext='mp4', width=2160, height=3840,
                            has_watermark=True, has_audio=False, audio_codec='none',
                            filesize=200_000_000)]
    if clean:
        formats.append(VideoFormat(format_id='clean', ext='mp4', width=1080, height=1920,
            has_watermark=False, has_audio=False, audio_codec='none', filesize=20_000_000))
    else:
        formats.append(VideoFormat(format_id='audio', ext='m4a', has_video=False,
            has_audio=True, video_codec='none', audio_codec='aac', filesize=1_000_000))
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@author/video/123', formats=formats)
    engine = SimpleNamespace(settings=Settings(DOWNLOAD_DIR=tmp_path, MAX_FILE_SIZE_MB=50),
        extractor=SimpleNamespace(extract=AsyncMock(return_value=Extraction(info, tmp_path / 'unused'))))
    assert await TelegramDownloadService(engine).analyze(str(info.webpage_url)) == info
    assert list(tmp_path.iterdir()) == []
