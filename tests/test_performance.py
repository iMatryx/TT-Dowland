"""Performance events must be safe, timed monotonically, and counted once."""
import asyncio
import json
import logging

import pytest

from bot.utils import performance
from bot.services.operations import WorkQueue


def events(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name == 'bot.performance']


@pytest.mark.parametrize('outcome', ['success', 'error', 'cancelled'])
def test_stage_records_safe_fields_and_monotonic_duration(caplog, monkeypatch, outcome):
    caplog.set_level(logging.INFO)
    ticks = iter([10.0, 12.5])
    monkeypatch.setattr(performance.time, 'monotonic', lambda: next(ticks))
    error = {'error': RuntimeError, 'cancelled': asyncio.CancelledError}.get(outcome)
    with performance.operation('https://www.tiktok.com/@personal/video/123?token=secret'):
        try:
            with performance.stage('download'):
                if error:
                    raise error('https://signed.example/?signature=private')
        except (RuntimeError, asyncio.CancelledError):
            pass
    event, = events(caplog)
    assert event['duration_seconds'] == 2.5
    assert event['result'] == outcome
    assert event['platform'] == 'tiktok'
    assert event['operation_id']
    assert 'personal' not in json.dumps(event)
    assert 'secret' not in json.dumps(event)
    assert 'private' not in json.dumps(event)


async def test_queue_wait_is_separate_and_shared_job_counted_once(caplog):
    caplog.set_level(logging.INFO)
    queue = WorkQueue(concurrency=1, capacity=2)
    started, release = asyncio.Event(), asyncio.Event()

    async def blocking():
        started.set()
        await release.wait()

    first = asyncio.create_task(queue.run('private-first', blocking))
    await started.wait()
    second = asyncio.create_task(queue.run('private-second', lambda: asyncio.sleep(0)))
    follower = asyncio.create_task(queue.run('private-second', lambda: asyncio.sleep(0)))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second, follower)
    await queue.close()
    waits = [e for e in events(caplog) if e.get('stage') == 'queue_wait']
    assert len(waits) == 2
    assert all(e['result'] == 'success' and e['duration_seconds'] >= 0 for e in waits)
    assert len({e['operation_id'] for e in waits}) == 2
    assert 'private' not in json.dumps(events(caplog))


def test_cache_counters_are_separate(caplog):
    caplog.set_level(logging.INFO)
    metadata = performance.CacheMetrics('metadata')
    telegram = performance.CacheMetrics('telegram')
    with performance.operation('https://youtu.be/abcdefghijk'):
        metadata.record(False)
        metadata.record(True)
        telegram.record(False)
    first, second, third = events(caplog)
    assert (second['hits'], second['misses'], second['hit_rate']) == (1, 1, 0.5)
    assert (third['hits'], third['misses'], third['hit_rate']) == (0, 1, 0)
    assert first['cache'] == second['cache'] == 'metadata'
    assert third['cache'] == 'telegram'


async def test_metadata_cache_reuses_analysis_expires_and_does_not_cache_errors(tmp_path, caplog):
    from bot.config import Settings
    from bot.models import VideoInfo, VideoFormat
    from bot.services.downloader import TikTokDownloader
    from bot.services.extraction import Extraction
    from bot.services.runtime import BotRuntime
    from bot.services.telegram_download import TelegramDownloadService
    caplog.set_level(logging.INFO)
    settings = Settings(DOWNLOAD_DIR=tmp_path, CACHE_DB_PATH=tmp_path / 'cache.db')
    runtime = BotRuntime(settings)
    calls = 0
    async def extract(url, workspace):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError('private exception')
        path = workspace / 'info.json'
        path.write_text('{}')
        return Extraction(VideoInfo(id='abcdefghijk', webpage_url=url,
            formats=[VideoFormat(has_watermark=False, format_id='source', ext='mp4')]), path)
    from types import SimpleNamespace
    engine = TikTokDownloader(settings, extractor=SimpleNamespace(extract=extract))
    now = [0.0]
    engine.extractor.clock = lambda: now[0]
    service = TelegramDownloadService(engine)
    url = 'https://www.youtube.com/shorts/abcdefghijk'
    try:
        first = await runtime.analyze(service, url)
        first.title = 'caller mutation'
        assert (await runtime.analyze(service, url)).title is None
        now[0] = 301
        with pytest.raises(ValueError):
            await runtime.analyze(service, url)
        await runtime.analyze(service, url)
        assert calls == 3
        lookups = [e for e in events(caplog) if e.get('cache') == 'metadata']
        assert [e['outcome'] for e in lookups] == ['miss', 'hit', 'miss', 'miss']
        assert lookups[-1]['hit_rate'] == 0.25
    finally:
        await engine.close()
        await runtime.close()


async def test_queued_cancellation_is_recorded_without_running_work(caplog):
    caplog.set_level(logging.INFO)
    queue = WorkQueue(concurrency=1, capacity=1)
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocking():
        entered.set()
        await release.wait()
    first = asyncio.create_task(queue.run('first', blocking))
    await entered.wait()
    second = asyncio.create_task(queue.run('second', lambda: asyncio.sleep(0)))
    await asyncio.sleep(0)
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)
    release.set()
    await first
    await queue.close()
    waits = [e for e in events(caplog) if e.get('stage') == 'queue_wait']
    assert sorted(e['result'] for e in waits) == ['cancelled', 'success']


def test_worker_conversion_is_excluded_from_download_duration(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    now = [10.0]
    monkeypatch.setattr(performance.time, 'monotonic', lambda: now[0])
    timing = tmp_path / 'timings.json'
    with performance.operation('https://www.instagram.com/reel/example/'):
        with performance.download_stage(timing):
            timing.write_text(json.dumps([{'started': 12.0, 'finished': 15.0, 'result': 'success'}]))
            now[0] = 20.0
    conversions = [e for e in events(caplog) if e['stage'] == 'conversion']
    downloads = [e for e in events(caplog) if e['stage'] == 'download']
    assert conversions[0]['duration_seconds'] == 3
    assert downloads[0]['duration_seconds'] == 7
    assert conversions[0]['operation_id'] == downloads[0]['operation_id']


async def test_worker_context_does_not_leak_between_platforms(caplog):
    caplog.set_level(logging.INFO)
    queue = WorkQueue(concurrency=1, capacity=1)
    @performance.measured('analysis')
    async def work():
        return 1
    try:
        with performance.operation('https://www.tiktok.com/@u/video/123'):
            await queue.run('one', work)
        with performance.operation('https://www.youtube.com/shorts/abcdefghijk'):
            await queue.run('two', work)
    finally:
        await queue.close()
    analyses = [e for e in events(caplog) if e.get('stage') == 'analysis']
    assert [e['platform'] for e in analyses] == ['tiktok', 'youtube']
    assert len({e['operation_id'] for e in analyses}) == 2


@pytest.mark.parametrize('result', ['error', 'cancelled'])
def test_interrupted_worker_conversion_remains_separate(tmp_path, monkeypatch, caplog, result):
    caplog.set_level(logging.INFO)
    now = [10.0]
    monkeypatch.setattr(performance.time, 'monotonic', lambda: now[0])
    timing = tmp_path / 'timings.json'
    exception = RuntimeError if result == 'error' else asyncio.CancelledError
    with pytest.raises(exception):
        with performance.download_stage(timing):
            timing.write_text(json.dumps([{'started': 12.0}]))
            now[0] = 15.0
            raise exception('private data')
    conversion, download = events(caplog)
    assert conversion['duration_seconds'] == 3
    assert download['duration_seconds'] == 2
    assert conversion['result'] == download['result'] == result


async def test_disabled_caches_do_not_report_hits_or_reuse_metadata(tmp_path, caplog):
    from types import SimpleNamespace
    from bot.config import Settings
    from bot.models import VideoInfo
    from bot.services.runtime import BotRuntime
    caplog.set_level(logging.INFO)
    runtime = BotRuntime(Settings(CACHE_ENABLED=False, DOWNLOAD_DIR=tmp_path, CACHE_DB_PATH=tmp_path / 'cache.db'))
    calls = 0
    url = 'https://www.tiktok.com/@u/video/123'
    async def analyze(url):
        nonlocal calls
        calls += 1
        return VideoInfo(id='123', webpage_url=url)
    try:
        for _ in range(2):
            await runtime.analyze(SimpleNamespace(analyze=analyze), url)
        assert calls == 2
        assert not [e for e in events(caplog) if e['event'] == 'cache_lookup']
    finally:
        await runtime.close()


async def test_shared_analysis_counts_one_cache_miss_when_one_waiter_cancels(tmp_path, caplog):
    from bot.config import Settings
    from bot.services.downloader import TikTokDownloader
    from bot.services.runtime import BotRuntime
    from bot.services.telegram_download import TelegramDownloadService
    from tests.test_analysis_cache import SourceExtractor
    caplog.set_level(logging.INFO)
    settings = Settings(DOWNLOAD_DIR=tmp_path, CACHE_DB_PATH=tmp_path / 'cache.db')
    runtime = BotRuntime(settings)
    source = SourceExtractor()
    source.gate = asyncio.Event()
    engine = TikTokDownloader(settings, extractor=source)
    service = TelegramDownloadService(engine)
    url = 'https://www.tiktok.com/@u/video/123'
    first = asyncio.create_task(runtime.analyze(service, url))
    await source.entered.wait()
    follower = asyncio.create_task(runtime.analyze(service, url))
    try:
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        source.gate.set()
        assert (await follower).id == '123'
        assert source.calls == 1
        cache_events = [e for e in events(caplog) if e.get('cache') == 'metadata']
        assert len(cache_events) == 1 and cache_events[0]['outcome'] == 'miss'
    finally:
        source.gate.set()
        await asyncio.gather(first, follower, return_exceptions=True)
        await engine.close()
        await runtime.close()


async def test_analysis_deadline_includes_waiting_for_slot(monkeypatch, caplog):
    from types import SimpleNamespace
    from bot.models.exceptions import DownloadTimeoutError
    from bot.services.telegram_download import TelegramDownloadService
    caplog.set_level(logging.INFO)
    service = TelegramDownloadService(SimpleNamespace(settings=SimpleNamespace(max_concurrent_downloads=1)))
    await service._analysis_slots.acquire()
    timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, 'timeout', lambda seconds: timeout(0.01))
    try:
        with pytest.raises(DownloadTimeoutError):
            await asyncio.wait_for(service.analyze('https://www.tiktok.com/@u/video/123'), 0.2)
    finally:
        service._analysis_slots.release()
    waiting, = events(caplog)
    assert waiting['stage'] == 'queue_wait' and waiting['queue'] == 'analysis'
    assert waiting['result'] == 'cancelled'
