"""Exercise preparation using real local media; replace only costly process failures."""
import asyncio
from pathlib import Path
import shutil
import subprocess

import pytest

from bot.models.exceptions import CorruptFileError, DownloadTimeoutError
from bot.services import preview
from bot.services.media import verify_media


@pytest.fixture
def h264_source(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Local FFmpeg/ffprobe required')
    source = tmp_path / 'source.mkv'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=black:s=64x64:d=1', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-shortest', str(source)], check=True)
    return source


async def test_remux_preserves_codecs_and_original(h264_source, tmp_path):
    original = h264_source.read_bytes()
    output = tmp_path / 'prepared.mp4'
    prepared = await preview.create_preview(h264_source, output, max_bytes=1_000_000, timeout=10)
    assert prepared.path == output
    assert prepared.format.remuxed and not prepared.format.transcoded
    assert prepared.format.video_codec == 'h264' and prepared.format.audio_codec == 'aac'
    assert h264_source.read_bytes() == original
    assert output.read_bytes().find(b'moov') < output.read_bytes().find(b'mdat')


async def test_compatible_mp4_reuses_original_without_ffmpeg(h264_source, tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(h264_source),
                    '-c', 'copy', '-movflags', '+faststart', str(source)], check=True)
    async def forbidden(*args, **kwargs):
        pytest.fail('Compatible verified MP4 must not invoke FFmpeg')
    monkeypatch.setattr(preview, 'run_process', forbidden)
    output = tmp_path / 'unused.mp4'
    prepared = await preview.create_preview(source, output, max_bytes=1_000_000, timeout=10)
    assert prepared.path == source
    assert not prepared.format.transcoded and not prepared.format.remuxed
    assert not output.exists()


@pytest.mark.parametrize('failure', ['exit', 'invalid', 'audio', 'duration', 'timeout'])
async def test_failed_or_invalid_remux_falls_back_to_verified_transcode(h264_source, tmp_path, monkeypatch, failure):
    real = preview.run_process
    attempts = []
    async def process(*args, **kwargs):
        attempts.append(args)
        if '-c' in args and args[args.index('-c') + 1] == 'copy':
            if failure == 'timeout':
                raise DownloadTimeoutError('Remux attempt exceeded its deadline')
            if failure == 'exit':
                Path(args[-1]).write_bytes(b'partial')
                return 1, b'', b'failure'
            if failure == 'invalid':
                Path(args[-1]).write_bytes(b'corrupt')
                return 0, b'', b''
            changed = list(args)
            if failure == 'audio':
                changed[changed.index('-map'):] = ['-map', '0:v:0', '-c', 'copy', str(args[-1])]
            else:
                changed[-1:-1] = ['-t', '0.1']
            return await real(*changed, **kwargs)
        return await real(*args, **kwargs)
    monkeypatch.setattr(preview, 'run_process', process)
    output = tmp_path / 'prepared.mp4'
    prepared = await preview.create_preview(h264_source, output, max_bytes=1_000_000, timeout=10)
    assert prepared.format.transcoded and not prepared.format.remuxed
    assert len(attempts) == 2
    assert prepared.format.has_audio
    assert (await verify_media(output)).video_codec == 'h264'


async def test_cancellation_during_remux_cleans_partial_and_preserves_source(h264_source, tmp_path, monkeypatch):
    entered = asyncio.Event()
    output = tmp_path / 'partial.mp4'
    async def blocked(*args, **kwargs):
        output.write_bytes(b'partial')
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(preview, 'run_process', blocked)
    task = asyncio.create_task(preview.create_preview(h264_source, output, max_bytes=1_000_000, timeout=10))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not output.exists() and h264_source.exists()


async def test_conversion_limit_and_waiter_cancellation(h264_source, tmp_path, monkeypatch):
    from bot.services.conversion import ConversionLimiter
    limiter = ConversionLimiter(1)
    entered, release = asyncio.Event(), asyncio.Event()
    real = preview.run_process
    active = peak = calls = 0
    async def process(*args, **kwargs):
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        entered.set()
        try:
            await release.wait()
            return await real(*args, **kwargs)
        finally:
            active -= 1
    monkeypatch.setattr(preview, 'run_process', process)
    first = asyncio.create_task(preview.create_preview(h264_source, tmp_path / 'one.mp4',
        max_bytes=1_000_000, timeout=10, conversions=limiter))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(preview.create_preview(h264_source, tmp_path / 'two.mp4',
        max_bytes=1_000_000, timeout=10, conversions=limiter))
    try:
        await asyncio.sleep(.05)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert calls == 1 and not (tmp_path / 'two.mp4').exists()
        release.set()
        await first
        await preview.create_preview(h264_source, tmp_path / 'three.mp4',
            max_bytes=1_000_000, timeout=10, conversions=limiter)
        assert peak == 1 and calls == 2
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)


async def test_conversion_backlog_does_not_block_downloads_or_analysis(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from bot.config import Settings
    from bot.models import DownloadResult, VideoInfo, VideoFormat
    from bot.services.conversion import ConversionLimiter
    from bot.services.runtime import BotRuntime
    from bot.services.telegram_sender import SentMedia
    runtime = BotRuntime(Settings(DOWNLOAD_DIR=tmp_path, CACHE_DB_PATH=tmp_path / 'cache.db',
                                  CACHE_ENABLED=False, MAX_CONCURRENT_DOWNLOADS=1))
    conversions = ConversionLimiter(1)
    entered, release, downloaded = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0
    source = VideoFormat(has_watermark=False, format_id='source', ext='mp4', video_codec='mpeg4')
    async def download(url, **kwargs):
        nonlocal calls
        calls += 1
        path = runtime.work_dir / f'original-{calls}.mp4'
        path.write_bytes(b'original')
        if calls == 3:
            downloaded.set()
        return DownloadResult(video_info=VideoInfo(id=str(calls), webpage_url=url, formats=[source]),
                              file_path=path, file_size_bytes=8, verified_format=source, format=source)
    async def send(*args, **kwargs):
        async with conversions.slot():
            entered.set()
            await release.wait()
            return SentMedia('video', None)
    info = VideoInfo(id='1', webpage_url='https://www.tiktok.com/@u/video/1', formats=[source])
    service = SimpleNamespace(download=download, analyze=AsyncMock(return_value=info))
    sender = SimpleNamespace(send_with_receipt=send)
    bot = SimpleNamespace(id=1)
    progress = SimpleNamespace(update=AsyncMock())
    tasks = []
    try:
        await runtime.start()
        tasks.append(asyncio.create_task(runtime.deliver(bot, 1, info, 'preview', service, sender, progress)))
        await asyncio.wait_for(entered.wait(), 1)
        for index in [2, 3]:
            other = info.model_copy(update={'id': str(index), 'webpage_url': f'https://www.tiktok.com/@u/video/{index}'})
            tasks.append(asyncio.create_task(runtime.deliver(bot, index, other, 'preview', service, sender, progress)))
        await asyncio.wait_for(downloaded.wait(), .5)
        assert (await asyncio.wait_for(runtime.analyze(service, str(info.webpage_url)), .5)).id == '1'
        release.set()
        await asyncio.gather(*tasks)
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.close()


async def test_same_output_is_rejected_without_touching_original(h264_source):
    original = h264_source.read_bytes()
    with pytest.raises(CorruptFileError):
        await preview.create_preview(h264_source, h264_source, max_bytes=1_000_000, timeout=10)
    assert h264_source.read_bytes() == original


async def test_conversion_wait_timeout_preserves_original(h264_source, tmp_path, monkeypatch):
    from bot.services.conversion import ConversionLimiter
    limiter = ConversionLimiter(1)
    output = tmp_path / 'prepared.mp4'
    async with limiter.slot():
        with pytest.raises(DownloadTimeoutError):
            await preview.create_preview(h264_source, output, max_bytes=1_000_000, timeout=.1,
                                         conversions=limiter)
    assert not output.exists() and h264_source.exists()
    # A deadline while queued must not leak a permit.
    assert (await preview.create_preview(h264_source, output, max_bytes=1_000_000,
                                        timeout=10, conversions=limiter)).format.remuxed


async def test_audio_conversion_wait_releases_download_slot(h264_source, tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from bot.config import Settings
    from bot.models import VideoInfo, VideoFormat
    from bot.services import downloader
    from bot.services.extraction import Extraction
    from bot.services.conversion import ConversionLimiter
    limiter = ConversionLimiter(1)
    transferred = asyncio.Event()
    async def extract(url, workspace):
        info_path = workspace / 'info.json'
        info_path.write_text('{}')
        return Extraction(VideoInfo(id='123', webpage_url=url, formats=[VideoFormat(has_watermark=False, 
            format_id='source', ext='mkv', has_video=True, has_audio=True)]), info_path)
    async def worker(action, payload, timeout):
        job = json.loads(payload.read_text())
        shutil.copyfile(h264_source, Path(job['media_dir']) / 'original.mkv')
        transferred.set()
    monkeypatch.setattr(downloader, 'run_worker', worker)
    engine = downloader.TikTokDownloader(Settings(DOWNLOAD_DIR=tmp_path / 'downloads',
        MAX_CONCURRENT_DOWNLOADS=1), extractor=SimpleNamespace(extract=extract), conversions=limiter)
    url = 'https://www.tiktok.com/@u/video/123'
    audio_task = None
    try:
        async with limiter.slot():
            audio_task = asyncio.create_task(engine.download(url, audio_only=True))
            await asyncio.wait_for(transferred.wait(), 1)
            video = await asyncio.wait_for(engine.download(url), 2)
            video.cleanup()
            assert not audio_task.done()
            audio_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await audio_task
        assert not list((tmp_path / 'downloads').iterdir())
    finally:
        if audio_task is not None and not audio_task.done():
            audio_task.cancel()
            await asyncio.gather(audio_task, return_exceptions=True)


async def test_audio_and_video_use_the_same_conversion_budget(h264_source, tmp_path, monkeypatch):
    from bot.services import audio
    from bot.services.conversion import ConversionLimiter
    limiter = ConversionLimiter(1)
    entered, release = asyncio.Event(), asyncio.Event()
    real = preview.run_process
    active = peak = 0
    async def process(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        try:
            await release.wait()
            return await real(*args, **kwargs)
        finally:
            active -= 1
    monkeypatch.setattr(preview, 'run_process', process)
    monkeypatch.setattr(audio, 'run_process', process)
    video_task = asyncio.create_task(preview.create_preview(h264_source, tmp_path / 'prepared.mp4',
        max_bytes=1_000_000, timeout=10, conversions=limiter))
    await asyncio.wait_for(entered.wait(), 1)
    audio_task = asyncio.create_task(audio.extract_audio(h264_source, tmp_path / 'audio.mp3',
        max_bytes=1_000_000, timeout=10, conversions=limiter))
    try:
        await asyncio.sleep(.05)
        assert active == 1
        release.set()
        video, sound = await asyncio.gather(video_task, audio_task)
        assert peak == 1
        assert video.format.remuxed and sound.transcoded
    finally:
        release.set()
        await asyncio.gather(video_task, audio_task, return_exceptions=True)


async def test_remux_receipt_and_cache_preserve_transformation_metadata(h264_source, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from bot.models import DownloadResult, VideoInfo
    from bot.services.cache import CacheEntry, SQLiteCache
    from bot.services.media_info import video_captions
    from bot.services.telegram_sender import TelegramSender
    original = h264_source.read_bytes()
    fmt = await verify_media(h264_source)
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@u/video/123')
    result = DownloadResult(video_info=info, file_path=h264_source, file_size_bytes=len(original),
                            verified_format=fmt, format=fmt)
    uploaded = []
    async def send(**kwargs):
        path = kwargs['video'].path
        uploaded.append(path)
        assert path != h264_source and path.suffix == '.mp4'
        assert (await verify_media(path)).video_codec == 'h264'
        return SimpleNamespace(video=SimpleNamespace(file_id='cached-preview'))
    bot = SimpleNamespace(send_video=AsyncMock(side_effect=send))
    receipt = await TelegramSender().send_with_receipt(bot, 1, result, 'preview')
    assert receipt.verified_format.remuxed and not receipt.verified_format.transcoded
    assert h264_source.read_bytes() == original and not uploaded[0].exists()
    _, caption = video_captions(info, receipt.verified_format, fmt, receipt.file_size_bytes)
    assert 'remuxat fără recodare' in caption and 'convertită în H.264' not in caption
    store = SQLiteCache(tmp_path / 'cache.db')
    try:
        await store.put(CacheEntry(bot_id=1, video_id='123', url=str(info.webpage_url), quality='preview',
            kind='video', file_id=receipt.file_id, size=receipt.file_size_bytes, source=fmt,
            verified=receipt.verified_format))
        entry = await store.get(1, '123', 'preview', 'video')
        assert entry.verified.remuxed and not entry.verified.transcoded
        assert entry.source.ext == 'mkv' and not entry.source.remuxed
    finally:
        await store.close()


async def test_configured_conversion_limit_allows_two_jobs_and_blocks_third():
    from bot.config import Settings
    from bot.telegram_config import create_dispatcher
    dispatcher = create_dispatcher(Settings(MAX_CONCURRENT_CONVERSIONS=2))
    limiter = dispatcher['sender'].conversions
    assert dispatcher['download_service'].engine.conversions is limiter
    entered, release = asyncio.Event(), asyncio.Event()
    active = peak = 0
    async def work():
        nonlocal active, peak
        async with limiter.slot():
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await release.wait()
            finally:
                active -= 1
    tasks = [asyncio.create_task(work()) for _ in range(3)]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert active == 2
        release.set()
        await asyncio.gather(*tasks)
        assert peak == 2
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await dispatcher['preferences'].close()


async def test_source_is_protected_from_cleaner_while_waiting_for_audio_conversion(h264_source, tmp_path):
    import os
    from bot.config import Settings
    from bot.models import DownloadResult, VideoInfo
    from bot.services.conversion import ConversionLimiter
    from bot.services.downloader import TikTokDownloader
    from bot.services.files import FileCleaner, FileRegistry
    source = tmp_path / 'tiktok-source.mkv'
    shutil.copyfile(h264_source, source)
    os.utime(source, (1, 1))
    registry, limiter = FileRegistry(), ConversionLimiter(1)
    engine = TikTokDownloader(Settings(DOWNLOAD_DIR=tmp_path), registry=registry, conversions=limiter)
    raw = DownloadResult(video_info=VideoInfo(id='123', webpage_url='https://www.tiktok.com/@u/video/123'),
                         file_path=source, file_size_bytes=source.stat().st_size,
                         verified_format=await verify_media(source))
    task = None
    try:
        async with limiter.slot():
            task = asyncio.create_task(engine._convert_audio(raw, quality='high'))
            await asyncio.sleep(0)
            assert await FileCleaner(tmp_path, registry, max_age=0).sweep() == 0
            assert source.exists()
        converted = await task
        assert converted.verified_format.transcoded
        converted.cleanup()
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_bound_verification_skips_source_probe_but_verifies_remux(h264_source, tmp_path, monkeypatch):
    verified = await verify_media(h264_source)
    real = preview.verify_media
    probed = []
    async def probe(path, **kwargs):
        probed.append(path)
        return await real(path, **kwargs)
    monkeypatch.setattr(preview, 'verify_media', probe)
    output = tmp_path / 'bound.mp4'
    prepared = await preview.create_preview(h264_source, output, max_bytes=1_000_000,
                                            timeout=10, verified=verified)
    assert probed == [output]
    assert prepared.format.remuxed
    prepared = await preview.create_preview(output, tmp_path / 'unused.mp4', max_bytes=1_000_000,
                                            timeout=10, verified=prepared.format)
    assert prepared.path == output and probed == [output]


@pytest.mark.parametrize('change', ['same_size', 'replace', 'metadata', 'unbound'])
async def test_stale_or_unbound_verification_is_reprobed(h264_source, tmp_path, monkeypatch, change):
    import os
    verified = await verify_media(h264_source)
    if change == 'same_size':
        old_stat = h264_source.stat()
        h264_source.write_bytes(h264_source.read_bytes())
        os.utime(h264_source, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    elif change == 'replace':
        replacement = tmp_path / 'replacement.mkv'
        replacement.write_bytes(h264_source.read_bytes())
        replacement.replace(h264_source)
    elif change == 'metadata':
        verified = verified.model_copy(update={'video_codec': 'vp9'})
    else:
        from bot.models import VideoFormat
        verified = VideoFormat.model_validate_json(verified.model_dump_json())
    real = preview.verify_media
    probed = []
    async def probe(path, **kwargs):
        probed.append(path)
        return await real(path, **kwargs)
    monkeypatch.setattr(preview, 'verify_media', probe)
    output = tmp_path / 'fresh.mp4'
    await preview.create_preview(h264_source, output, max_bytes=1_000_000, timeout=10, verified=verified)
    assert probed == [h264_source, output]


async def test_sender_rejects_same_size_change_after_verification(h264_source):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from bot.models import DownloadResult, VideoInfo
    from bot.services.telegram_sender import TelegramSender
    verified = await verify_media(h264_source)
    result = DownloadResult(video_info=VideoInfo(id='123', webpage_url='https://www.tiktok.com/@u/video/123'),
        file_path=h264_source, file_size_bytes=h264_source.stat().st_size, verified_format=verified)
    h264_source.write_bytes(h264_source.read_bytes())
    bot = SimpleNamespace(send_document=AsyncMock())
    with pytest.raises(CorruptFileError, match='changed'):
        await TelegramSender().send_with_receipt(bot, 1, result, 'document')
    bot.send_document.assert_not_awaited()
