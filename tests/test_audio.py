import asyncio
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.config import Settings
from bot.models import DownloadResult, VideoFormat, VideoInfo
from bot.models.exceptions import CorruptFileError, FileSizeLimitError, VideoUnavailableError
from bot.handlers.keyboards import available_actions
from bot.services import downloader
from bot.services.extraction import Extraction
from bot.services.telegram_sender import TelegramSender

URL = 'https://www.youtube.com/shorts/AbCd_123-xy'


def test_audio_selects_separate_stream_and_respects_quality():
    from bot.services.quality import select_audio
    muxed = VideoFormat(has_watermark=False, format_id='video', ext='mp4', has_video=True, has_audio=True, tbr=5000)
    low = VideoFormat(has_watermark=False, format_id='low', ext='m4a', has_video=False, has_audio=True, tbr=64)
    high = low.model_copy(update={'format_id': 'high', 'tbr': 192})
    assert select_audio([muxed, low, high]).format_id == 'high'
    with pytest.raises(TypeError):
        select_audio([muxed, low, high], quality='low')
    assert select_audio([muxed]).format_id == 'video'
    with pytest.raises(VideoUnavailableError):
        select_audio([muxed.model_copy(update={'has_audio': False, 'audio_codec': 'none'})])


def test_audio_button_is_video_only_and_hidden_for_known_silence():
    source = VideoFormat(has_watermark=False, format_id='video', ext='mp4', has_audio=True)
    info = VideoInfo(id='same', webpage_url=URL, formats=[source])
    assert ('audio', '🎵 Doar audio') in available_actions(info)
    source.has_audio = False
    source.audio_codec = 'none'
    assert 'audio' not in dict(available_actions(info))


@pytest.fixture
def media_fixture(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg and ffprobe required for local audio integration')
    path = tmp_path / 'fixture.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=black:s=64x64:d=1', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=1', '-c:v', 'mpeg4', '-c:a', 'aac',
                    '-shortest', str(path)], check=True)
    return path


@pytest.mark.asyncio
async def test_audio_download_converts_verifies_and_cleans(tmp_path, media_fixture, monkeypatch):
    source = VideoFormat(has_watermark=False, format_id='muxed', ext='mp4', has_audio=True, has_video=True)
    async def extract(url, workspace):
        metadata = workspace / 'info.json'
        metadata.write_text('{}')
        return Extraction(VideoInfo(id='clip', webpage_url=url, duration_seconds=1, formats=[source]), metadata)
    async def worker(action, payload, timeout):
        job = json.loads(payload.read_text())
        assert job['video_id'] == 'muxed' and job['audio_id'] is None
        shutil.copyfile(media_fixture, Path(job['media_dir']) / 'original.mp4')
    monkeypatch.setattr(downloader, 'run_worker', worker)
    output = tmp_path / 'downloads'
    engine = downloader.TikTokDownloader(Settings(DOWNLOAD_DIR=output), extractor=SimpleNamespace(extract=extract))
    result = await engine.download(URL, audio_only=True)
    assert result.audio_only and result.file_path.suffix == '.mp3'
    assert result.verified_format.has_audio and result.verified_format.has_video is False
    assert result.verified_format.duration_seconds == pytest.approx(1, abs=0.15)
    assert list(output.iterdir()) == [result.file_path]
    bot = SimpleNamespace(send_audio=AsyncMock(return_value=SimpleNamespace(audio=SimpleNamespace(file_id='sound-id'))))
    receipt = await TelegramSender().send_with_receipt(bot, 10, result, 'audio', message_thread_id=7)
    assert receipt.kind == 'audio' and receipt.file_id == 'sound-id'
    assert bot.send_audio.call_args.kwargs['message_thread_id'] == 7
    result.cleanup()
    assert not list(output.iterdir())


@pytest.mark.asyncio
async def test_audio_conversion_rejects_oversize_and_cleans(tmp_path, media_fixture):
    from bot.services.audio import extract_audio
    output = tmp_path / 'sound.mp3'
    with pytest.raises(FileSizeLimitError):
        await extract_audio(media_fixture, output, max_bytes=100, timeout=10)
    assert not output.exists()


@pytest.mark.asyncio
async def test_audio_send_rejects_video_artifact(tmp_path):
    path = tmp_path / 'original.mp4'
    path.write_bytes(b'video')
    result = DownloadResult(video_info=VideoInfo(id='clip', webpage_url=URL),
                            file_path=path, file_size_bytes=5)
    bot = SimpleNamespace(send_audio=AsyncMock())
    with pytest.raises(CorruptFileError):
        await TelegramSender().send_with_receipt(bot, 10, result, 'audio')
    bot.send_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_audio_conversion_cancellation_removes_partial_file(tmp_path, monkeypatch):
    from bot.services import audio
    output = tmp_path / 'partial.mp3'
    started = asyncio.Event()
    async def stalled(*args, **kwargs):
        output.write_bytes(b'partial')
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(audio, 'run_process', stalled)
    monkeypatch.setattr(audio, 'require_media_tools', lambda: None)
    task = asyncio.create_task(audio.extract_audio(tmp_path / 'input.mp4', output, max_bytes=100, timeout=10))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not output.exists()


def test_small_audio_remains_available_when_video_is_too_big():
    info = VideoInfo(id='clip', webpage_url=URL, formats=[
        VideoFormat(has_watermark=False, format_id='video', ext='mp4', has_audio=False, filesize=100_000_000),
        VideoFormat(has_watermark=False, format_id='sound', ext='m4a', has_video=False, has_audio=True, filesize=1000),
    ])
    assert available_actions(info, max_bytes=50_000_000) == [('audio', '🎵 Doar audio')]


def test_audio_still_available_when_no_clean_video_exists():
    info = VideoInfo(id='clip', webpage_url=URL, formats=[
        VideoFormat(has_watermark=True, format_id='video', ext='mp4', has_audio=True),
    ])
    assert available_actions(info) == [('audio', '🎵 Doar audio')]
