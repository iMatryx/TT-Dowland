"""Resource budgets and recovery; only local temporary fixtures."""
import json
import os

from bot.models import VideoInfo, VideoFormat
from bot.services.analysis_cache import CachedExtractor
from bot.services.extraction import Extraction
from bot.services.files import FileCleaner, FileRegistry


class MetadataSource:
    def __init__(self, padding='x' * 10_000):
        self.calls = 0
        self.padding = padding

    async def extract(self, url, workspace):
        self.calls += 1
        path = workspace / 'info.json'
        path.write_text(json.dumps({'id': '1', 'padding': self.padding}, ensure_ascii=False))
        return Extraction(VideoInfo(id='1', webpage_url=url, formats=[VideoFormat(
            format_id='source', ext='mp4', has_watermark=False)]), path)


async def test_cache_evicts_lru_by_memory_and_releases_budget_on_invalidation(tmp_path):
    source = MetadataSource()
    cache = CachedExtractor(source, root=tmp_path / 'cache', max_bytes=25_000)
    urls = [f'https://www.tiktok.com/@u/video/{n}' for n in range(1, 4)]
    try:
        first = await cache.extract(urls[0], tmp_path)
        await cache.extract(urls[1], tmp_path)
        await cache.extract(urls[0], tmp_path)  # Touch without extending its TTL.
        await cache.extract(urls[2], tmp_path)
        assert source.calls == 3 and len(cache._items) == 2
        assert cache.cached_bytes <= 25_000
        assert urls[1] not in cache._items and urls[0] in cache._items
        before = cache.cached_bytes
        cache.invalidate(urls[0], 'wrong-generation')
        assert cache.cached_bytes == before
        cache.invalidate(urls[0], first.cache_token)
        assert cache.cached_bytes < before
    finally:
        await cache.close()
    assert cache.cached_bytes == 0


async def test_serialized_metadata_larger_than_budget_is_returned_without_retention(tmp_path):
    # ASCII escaping expands this payload; measure the retained representation.
    source = MetadataSource('ș' * 1000)
    cache = CachedExtractor(source, root=tmp_path / 'cache', max_bytes=4000)
    try:
        first = await cache.extract('https://www.tiktok.com/@u/video/1', tmp_path)
        assert json.loads(first.info_path.read_text())['padding'] == 'ș' * 1000
        assert not cache._items and cache.cached_bytes == 0
        await cache.extract('https://www.tiktok.com/@u/video/1', tmp_path)
        assert source.calls == 2
    finally:
        await cache.close()


async def test_cache_expiry_frees_memory_budget(tmp_path):
    now = [0]
    cache = CachedExtractor(MetadataSource(), root=tmp_path / 'cache', max_bytes=15_000,
                            ttl=5, clock=lambda: now[0])
    try:
        await cache.extract('https://www.tiktok.com/@u/video/1', tmp_path)
        first = cache.cached_bytes
        now[0] = 6
        await cache.extract('https://www.tiktok.com/@u/video/2', tmp_path)
        assert len(cache._items) == 1 and cache.cached_bytes == first
    finally:
        await cache.close()


async def test_recovery_removes_orphaned_preview_but_preserves_active_and_unrelated(tmp_path):
    registry = FileRegistry()
    orphan = tmp_path / '.preview-orphan'
    active = tmp_path / '.preview-active'
    recent = tmp_path / '.preview-recent'
    unrelated = tmp_path / 'keep'
    for path in [orphan, active, recent, unrelated]:
        path.mkdir()
        (path / 'dummy').write_bytes(b'x')
    for path in [orphan, active]:
        os.utime(path, (1, 1))
    link = tmp_path / '.preview-link'
    link.symlink_to(unrelated, target_is_directory=True)
    cleaner = FileCleaner(tmp_path, registry, max_age=60)
    with registry.track(active):
        assert await cleaner.sweep() == 1
        assert active.exists() and recent.exists()
    assert await cleaner.sweep(recovery=True) == 2
    assert unrelated.exists() and link.is_symlink()


async def test_active_preview_directory_is_protected_from_periodic_cleanup(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from bot.models import DownloadResult
    from bot.services import preview
    from bot.services.telegram_sender import TelegramSender
    registry = FileRegistry()
    source = tmp_path / 'tiktok-source.mkv'
    source.write_bytes(b'original')
    info = VideoInfo(id='1', webpage_url='https://www.tiktok.com/@u/video/1')
    result = DownloadResult(video_info=info, file_path=source, file_size_bytes=8,
        verified_format=VideoFormat(format_id='verified', ext='mkv', video_codec='mpeg4'))
    entered = asyncio.Event()
    staging = []
    async def prepare(src, output, **kwargs):
        output.write_bytes(b'partial')
        staging.append(output.parent)
        os.utime(output.parent, (1, 1))
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(preview, 'create_preview', prepare)
    sender = TelegramSender(registry=registry)
    task = asyncio.create_task(sender.send_with_receipt(SimpleNamespace(), 1, result, 'preview'))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with registry.track(source):
            assert await FileCleaner(tmp_path, registry, max_age=0).sweep() == 0
        assert staging[0].exists()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not staging[0].exists() and source.exists()


async def test_active_zip_upload_directory_is_protected_until_cancellation(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from bot.models import DownloadResult
    from bot.services.telegram_sender import TelegramSender
    registry = FileRegistry()
    source = tmp_path / 'tiktok-source.mp4'
    source.write_bytes(b'original')
    result = DownloadResult(video_info=VideoInfo(id='1', webpage_url='https://www.tiktok.com/@u/video/1'),
                            file_path=source, file_size_bytes=8)
    entered = asyncio.Event()
    staging = []
    async def upload(**kwargs):
        directory = kwargs['document'].path.parent
        staging.append(directory)
        os.utime(directory, (1, 1))
        entered.set()
        await asyncio.Event().wait()
    sender = TelegramSender(registry=registry)
    task = asyncio.create_task(sender.send_with_receipt(SimpleNamespace(send_document=upload), 1, result, 'zip'))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with registry.track(source):
            assert await FileCleaner(tmp_path, registry, max_age=0).sweep() == 0
        assert staging[0].exists()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not staging[0].exists() and source.exists()
