"""Immutable extraction snapshots, bounded reuse, and isolated cancellation."""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.models import VideoInfo, VideoFormat
from bot.models.exceptions import NetworkError, InvalidURLError
from bot.services.extraction import Extraction
from bot.services.analysis_cache import CachedExtractor

URL = 'https://www.tiktok.com/@u/video/123'


class SourceExtractor:
    def __init__(self):
        self.calls = 0
        self.paths = []
        self.gate = None
        self.entered = asyncio.Event()
        self.fail = False

    async def extract(self, url, workspace):
        self.calls += 1
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise NetworkError('temporary extraction failure')
        path = workspace / 'info.json'
        self.paths.append(path)
        path.write_text(json.dumps({'id': '123', 'formats': [
            {'format_id': 'source', 'url': f'https://cdn.example/{self.calls}?signature=private'}]}))
        return Extraction(VideoInfo(id='123', webpage_url=url, formats=[
            VideoFormat(has_watermark=False, format_id='source', ext='mp4', width=720)]), path)


@pytest.fixture
def cached(tmp_path):
    source = SourceExtractor()
    cache = CachedExtractor(source, root=tmp_path / 'cache-work', ttl=300, capacity=2)
    return source, cache


async def test_snapshot_outlives_workspace_and_is_independent(cached, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger='bot.performance')
    source, cache = cached
    one, two = tmp_path / 'one', tmp_path / 'two'
    one.mkdir(); two.mkdir()
    try:
        first = await cache.extract(URL, one)
        first.video.formats[0].width = 1
        first.info_path.write_text('{}')
        first.info_path.unlink()
        second = await cache.extract(URL, two)
        assert source.calls == 1
        assert all(not path.exists() for path in source.paths)
        assert second.cached and second.video.formats[0].width == 720
        assert json.loads(second.info_path.read_text())['formats'][0]['url'].endswith('signature=private')
        metrics = [json.loads(r.message) for r in caplog.records if r.name == 'bot.performance']
        assert [e['outcome'] for e in metrics if e['event'] == 'cache_lookup'] == ['miss', 'hit']
        assert 'signature' not in json.dumps(metrics)
    finally:
        await cache.close()


async def test_ttl_and_lru_are_bounded(cached, tmp_path):
    source, cache = cached
    now = [0.0]
    cache.clock = lambda: now[0]
    try:
        await cache.extract(URL, tmp_path)
        await cache.extract(URL.replace('123', '124'), tmp_path)
        await cache.extract(URL, tmp_path)  # Touch LRU without extending TTL.
        await cache.extract(URL.replace('123', '125'), tmp_path)
        await cache.extract(URL.replace('123', '124'), tmp_path)
        assert source.calls == 4
        now[0] = 301
        await cache.extract(URL.replace('123', '124'), tmp_path)
        assert source.calls == 5
    finally:
        await cache.close()


async def test_shared_analysis_survives_one_waiter_cancel(cached, tmp_path):
    source, cache = cached
    source.gate = asyncio.Event()
    first = asyncio.create_task(cache.extract(URL, tmp_path))
    await source.entered.wait()
    second_dir = tmp_path / 'second'; second_dir.mkdir()
    second = asyncio.create_task(cache.extract(URL, second_dir))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    source.gate.set()
    result = await second
    assert source.calls == 1 and result.info_path.exists()
    await cache.close()


async def test_last_waiter_cancel_cleans_and_does_not_cache(cached, tmp_path):
    source, cache = cached
    source.gate = asyncio.Event()
    task = asyncio.create_task(cache.extract(URL, tmp_path))
    await source.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    source.gate = None
    await cache.extract(URL, tmp_path)
    assert source.calls == 2
    assert not list((tmp_path / 'cache-work').iterdir())
    await cache.close()


async def test_errors_and_invalid_urls_are_not_cached(cached, tmp_path):
    source, cache = cached
    source.fail = True
    for _ in range(2):
        with pytest.raises(NetworkError):
            await cache.extract(URL, tmp_path)
    with pytest.raises(InvalidURLError):
        await cache.extract('http://127.0.0.1/video/123', tmp_path)
    assert source.calls == 2
    await cache.close()


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    from bot.config import Settings
    from bot.services import downloader
    from bot.services.telegram_download import TelegramDownloadService
    source = SourceExtractor()
    settings = Settings(DOWNLOAD_DIR=tmp_path / 'downloads', CACHE_DB_PATH=tmp_path / 'cache.db')
    engine = downloader.TikTokDownloader(settings, extractor=source)
    monkeypatch.setattr(downloader, 'require_media_tools', lambda **kwargs: None)
    async def verify(path, **kwargs):
        return VideoFormat(has_watermark=False, format_id='ffprobe', ext='mp4', width=720, has_audio=False)
    monkeypatch.setattr(downloader, 'verify_media', verify)
    return source, engine, TelegramDownloadService(engine), downloader


async def test_download_reuses_analysis_and_refreshes_expired_media_once(pipeline, monkeypatch):
    from bot.models.exceptions import VideoUnavailableError
    source, engine, service, module = pipeline
    transfers = []
    async def worker(action, payload, timeout):
        data = json.loads(payload.read_text())
        raw = json.loads(Path(data['info_path']).read_text())
        url = raw['formats'][0]['url']
        transfers.append(url)
        if '/1?' in url:
            raise VideoUnavailableError('Access denied by source')
        (Path(data['media_dir']) / 'original.mp4').write_bytes(b'original')
    monkeypatch.setattr(module, 'run_worker', worker)
    await service.analyze(URL)
    result = await service.download(URL)
    try:
        assert source.calls == 2
        assert len(transfers) == 2 and '/2?' in transfers[-1]
        assert result.file_path.read_bytes() == b'original'
    finally:
        result.cleanup()
        await engine.close()


async def test_failed_refresh_is_not_multiplied_by_runtime_retry(pipeline, monkeypatch, tmp_path):
    from bot.config import Settings
    from bot.models.exceptions import NetworkError
    from bot.services.runtime import BotRuntime
    source, engine, service, module = pipeline
    transfers = 0
    async def worker(*args):
        nonlocal transfers
        transfers += 1
        raise NetworkError('Expired source')
    monkeypatch.setattr(module, 'run_worker', worker)
    info = await service.analyze(URL)
    runtime = BotRuntime(Settings(DOWNLOAD_DIR=tmp_path / 'runtime', CACHE_DB_PATH=tmp_path / 'runtime.db'))
    try:
        await runtime.start()
        with pytest.raises(NetworkError):
            await runtime.deliver(SimpleNamespace(id=1), 10, info, 'document', service,
                                  SimpleNamespace(), SimpleNamespace(update=AsyncMock()))
        assert source.calls == 2 and transfers == 2
    finally:
        await runtime.close()
        await engine.close()


@pytest.mark.parametrize('failure', ['disk', 'limit', 'unsafe'])
async def test_local_and_security_failures_do_not_refresh(pipeline, monkeypatch, failure):
    from bot.models.exceptions import InsufficientSpaceError, FileSizeLimitError, UnsafeAddressError
    source, engine, service, module = pipeline
    error = {'disk': InsufficientSpaceError, 'limit': FileSizeLimitError, 'unsafe': UnsafeAddressError}[failure]
    async def worker(*args):
        raise error('failure')
    monkeypatch.setattr(module, 'run_worker', worker)
    await service.analyze(URL)
    try:
        with pytest.raises(error):
            await service.download(URL)
        assert source.calls == 1
    finally:
        await engine.close()


async def test_successful_cached_download_does_not_extract_again(pipeline, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='bot.performance')
    source, engine, service, module = pipeline
    raw_infos = []
    async def worker(action, payload, timeout):
        data = json.loads(payload.read_text())
        raw_infos.append(json.loads(Path(data['info_path']).read_text()))
        (Path(data['media_dir']) / 'original.mp4').write_bytes(b'original')
    monkeypatch.setattr(module, 'run_worker', worker)
    await service.analyze(URL)
    result = await service.download(URL)
    try:
        assert source.calls == 1 and len(raw_infos) == 1
        assert '/1?' in raw_infos[0]['formats'][0]['url']
        lookups = [json.loads(r.message) for r in caplog.records if r.name == 'bot.performance'
                   and json.loads(r.message)['event'] == 'cache_lookup']
        assert [e['outcome'] for e in lookups] == ['miss', 'hit']
        assert lookups[-1]['hit_rate'] == .5
    finally:
        result.cleanup()
        await engine.close()


async def test_upload_error_does_not_refresh_analysis(pipeline, monkeypatch, tmp_path):
    from bot.config import Settings
    from bot.models.exceptions import TelegramDeliveryError
    from bot.services.runtime import BotRuntime
    source, engine, service, module = pipeline
    async def worker(action, payload, timeout):
        job = json.loads(payload.read_text())
        (Path(job['media_dir']) / 'original.mp4').write_bytes(b'original')
    monkeypatch.setattr(module, 'run_worker', worker)
    info = await service.analyze(URL)
    runtime = BotRuntime(Settings(DOWNLOAD_DIR=tmp_path / 'runtime', CACHE_DB_PATH=tmp_path / 'cache.db'))
    try:
        await runtime.start()
        with pytest.raises(TelegramDeliveryError):
            await runtime.deliver(SimpleNamespace(id=1), 10, info, 'document', service,
                SimpleNamespace(send_with_receipt=AsyncMock(side_effect=TelegramDeliveryError('upload failed'))),
                SimpleNamespace(update=AsyncMock()))
        assert source.calls == 1
    finally:
        await runtime.close()
        await engine.close()


async def test_simultaneous_stale_downloads_share_one_refresh(pipeline, monkeypatch):
    from bot.models.exceptions import VideoUnavailableError
    source, engine, service, module = pipeline
    stale_entered = 0
    both = asyncio.Event()
    transfers = []
    async def worker(action, payload, timeout):
        nonlocal stale_entered
        job = json.loads(payload.read_text())
        url = json.loads(Path(job['info_path']).read_text())['formats'][0]['url']
        transfers.append(url)
        if '/1?' in url:
            stale_entered += 1
            if stale_entered == 2:
                both.set()
            await both.wait()
            raise VideoUnavailableError('Expired media URL')
        (Path(job['media_dir']) / 'original.mp4').write_bytes(b'original')
    monkeypatch.setattr(module, 'run_worker', worker)
    await service.analyze(URL)
    try:
        results = await asyncio.wait_for(asyncio.gather(service.download(URL), service.download(URL)), 2)
        assert source.calls == 2 and len(transfers) == 4
        for result in results:
            result.cleanup()
    finally:
        await engine.close()


async def test_late_invalidation_does_not_evict_new_snapshot(cached, tmp_path):
    source, cache = cached
    old = await cache.extract(URL, tmp_path)
    cache.invalidate(URL, old.cache_token)
    fresh = await cache.extract(URL, tmp_path)
    cache.invalidate(URL, old.cache_token)
    current = await cache.extract(URL, tmp_path)
    assert current.cache_token == fresh.cache_token and source.calls == 2
    await cache.close()


async def test_aliases_validate_and_share_canonical_entry(cached, tmp_path):
    source, cache = cached
    await cache.extract(URL, tmp_path)
    result = await cache.extract('https://tiktok.com/@u/video/123?tracking=secret', tmp_path)
    assert result.cached and source.calls == 1
    with pytest.raises(InvalidURLError):
        await cache.extract('https://user:password@tiktok.com/@u/video/123', tmp_path)
    await cache.close()


async def test_disabled_cache_still_coalesces_inflight_but_does_not_retain(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    source = SourceExtractor()
    cache = CachedExtractor(source, root=tmp_path / 'work', enabled=False)
    source.gate = asyncio.Event()
    first = asyncio.create_task(cache.extract(URL, tmp_path))
    await source.entered.wait()
    second = asyncio.create_task(cache.extract(URL, tmp_path))
    await asyncio.sleep(0)
    source.gate.set()
    await asyncio.gather(first, second)
    assert source.calls == 1
    await cache.extract(URL, tmp_path)
    assert source.calls == 2
    assert not [r for r in caplog.records if r.name == 'bot.performance' and 'cache_lookup' in r.message]
    await cache.close()


async def test_cold_transfer_failure_is_not_retried_as_cache_recovery(pipeline, monkeypatch):
    source, engine, service, module = pipeline
    transfers = 0
    async def worker(*args):
        nonlocal transfers
        transfers += 1
        raise NetworkError('source unreachable')
    monkeypatch.setattr(module, 'run_worker', worker)
    try:
        with pytest.raises(NetworkError):
            await service.download(URL)
        assert source.calls == 1 and transfers == 1
    finally:
        await engine.close()


async def test_photo_download_reuses_and_refreshes_cached_urls(tmp_path, monkeypatch):
    from bot.config import Settings
    from bot.models import PhotoInfo
    from bot.models.exceptions import VideoUnavailableError
    from bot.services.downloader import TikTokDownloader
    from bot.services.telegram_download import TelegramDownloadService
    from bot.services import photos
    calls = 0
    async def extract(url, workspace):
        nonlocal calls
        calls += 1
        path = workspace / 'info.json'
        path.write_text('{}')
        return Extraction(VideoInfo(id='123', webpage_url=url, media_type='photo', photos=[
            PhotoInfo(has_watermark=False, urls=[f'https://cdn.example/{calls}.jpg'], width=10, height=10)]), path)
    async def worker(action, payload, timeout):
        job = json.loads(payload.read_text())
        if '/1.jpg' in job['photos'][0]['urls'][0]:
            raise VideoUnavailableError('Expired image URL')
        (Path(job['media_dir']) / '0001.img').write_bytes(b'photo')
    async def verify(*args, **kwargs):
        return VideoFormat(has_watermark=False, format_id='probe', ext='jpg', video_codec='mjpeg', width=10, height=10)
    monkeypatch.setattr(photos, 'run_worker', worker)
    monkeypatch.setattr(photos, 'verify_media', verify)
    engine = TikTokDownloader(Settings(DOWNLOAD_DIR=tmp_path), extractor=SimpleNamespace(extract=extract))
    service = TelegramDownloadService(engine)
    await service.analyze(URL)
    result = await service.download(URL)
    try:
        assert calls == 2 and result.photos[0].file_path.read_bytes() == b'photo'
    finally:
        result.cleanup()
        await engine.close()


async def test_snapshot_drops_local_path_fields(cached, tmp_path):
    source, cache = cached
    extract = source.extract
    async def with_paths(url, workspace):
        result = await extract(url, workspace)
        data = json.loads(result.info_path.read_text())
        data['filepath'] = str(workspace / 'deleted.bin')
        data['formats'][0]['_filename'] = str(workspace / 'deleted-format.bin')
        result.info_path.write_text(json.dumps(data))
        return result
    source.extract = with_paths
    result = await cache.extract(URL, tmp_path)
    data = json.loads(result.info_path.read_text())
    assert 'filepath' not in data and '_filename' not in data['formats'][0]
    assert 'deleted' not in result.info_path.read_text()
    await cache.close()


async def test_shutdown_cancels_pending_extraction_and_rejects_new_work(cached, tmp_path):
    from bot.models.exceptions import QueueFullError
    source, cache = cached
    source.gate = asyncio.Event()
    task = asyncio.create_task(cache.extract(URL, tmp_path))
    await source.entered.wait()
    await cache.close()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(QueueFullError):
        await cache.extract(URL, tmp_path)
    assert not list((tmp_path / 'cache-work').iterdir())


async def test_inflight_capacity_is_bounded_and_duplicates_can_join(tmp_path):
    from bot.models.exceptions import QueueFullError
    source = SourceExtractor()
    source.gate = asyncio.Event()
    cache = CachedExtractor(source, root=tmp_path / 'work', concurrency=1, max_pending=1)
    first = asyncio.create_task(cache.extract(URL, tmp_path))
    await source.entered.wait()
    queued = asyncio.create_task(cache.extract(URL.replace('123', '124'), tmp_path))
    duplicate = asyncio.create_task(cache.extract(URL, tmp_path))
    await asyncio.sleep(0)
    try:
        with pytest.raises(QueueFullError):
            await cache.extract(URL.replace('123', '125'), tmp_path)
        source.gate.set()
        await asyncio.gather(first, queued, duplicate)
        assert source.calls == 2
    finally:
        source.gate.set()
        await cache.close()
        await asyncio.gather(first, queued, duplicate, return_exceptions=True)


@pytest.mark.parametrize('message', ['Permission denied', 'Read-only file system', 'Input/output error',
                                    '[Errno 2] No such file or directory'])
async def test_worker_disk_failures_do_not_invalidate_cache(pipeline, monkeypatch, message):
    from bot.models.exceptions import ApplicationError
    from bot.services import extraction
    source, engine, service, module = pipeline
    attempts = 0
    async def process(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return 1, json.dumps({'error': message}).encode(), b''
    monkeypatch.setattr(extraction, 'run_process', process)
    await service.analyze(URL)
    try:
        with pytest.raises(ApplicationError):
            await service.download(URL)
        assert source.calls == 1 and attempts == 1
    finally:
        await engine.close()


async def test_failed_fresh_extraction_after_stale_transfer_is_not_retried(pipeline, monkeypatch, tmp_path):
    from bot.config import Settings
    from bot.services.runtime import BotRuntime
    source, engine, service, module = pipeline
    transfers = 0
    async def worker(*args):
        nonlocal transfers
        transfers += 1
        source.fail = True
        raise NetworkError('Expired media URL')
    monkeypatch.setattr(module, 'run_worker', worker)
    info = await service.analyze(URL)
    runtime = BotRuntime(Settings(DOWNLOAD_DIR=tmp_path / 'runtime', CACHE_DB_PATH=tmp_path / 'cache.db'))
    try:
        await runtime.start()
        with pytest.raises(NetworkError):
            await runtime.deliver(SimpleNamespace(id=1), 10, info, 'document', service,
                                  SimpleNamespace(), SimpleNamespace(update=AsyncMock()))
        assert source.calls == 2 and transfers == 1
    finally:
        await runtime.close()
        await engine.close()


async def test_immediate_invalidation_retry_ignores_completed_shared_load(cached, tmp_path):
    source, cache = cached
    old = await cache.extract(URL, tmp_path)
    async def invalidate_and_reload():
        hit = await cache.extract(URL, tmp_path)
        cache.invalidate(URL, hit.cache_token)
        return await cache.extract(URL, tmp_path)
    refreshed, other_waiter = await asyncio.gather(invalidate_and_reload(), cache.extract(URL, tmp_path))
    assert refreshed.cache_token != old.cache_token
    assert source.calls == 2
    await cache.close()
