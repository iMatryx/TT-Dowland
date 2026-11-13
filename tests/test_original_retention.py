import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.models import DownloadResult, VideoInfo
from bot.services.files import FileCleaner, FileRegistry, SharedDownloads
from tests.test_runtime import setup_runtime, deliver
from tests.test_audio import media_fixture


def make_download(tmp_path):
    calls = []
    async def download():
        path = tmp_path / f'tiktok-original-{len(calls)}.mp4'
        path.write_bytes(b'original')
        calls.append(path)
        return DownloadResult(file_path=path, file_size_bytes=8,
            video_info=VideoInfo(id='123', webpage_url='https://www.tiktok.com/@u/video/123'))
    return calls, download


@pytest.mark.asyncio
async def test_original_reused_for_180_seconds_then_deleted(tmp_path):
    clock = SimpleNamespace(now=0.0)
    registry = FileRegistry()
    files = SharedDownloads(registry, clock=lambda: clock.now)
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('source', download) as result:
            files.retain('source', seconds=180)
        assert result.file_path.exists() and result.file_path.resolve() in registry.snapshot()
        assert await FileCleaner(tmp_path, registry).sweep(recovery=True) == 0
        clock.now = 179.9
        async with files.acquire('source', download) as same:
            assert same.file_path == result.file_path
        assert len(calls) == 1
        clock.now = 180.0
        await files.expire()
        assert not result.file_path.exists() and not registry.snapshot()
        async with files.acquire('source', download) as fresh:
            assert fresh.file_path != result.file_path
        assert len(calls) == 2 and not fresh.file_path.exists()
    finally:
        await files.close()


@pytest.mark.asyncio
async def test_expiry_waits_for_active_original_upload(tmp_path):
    clock = SimpleNamespace(now=0.0)
    files = SharedDownloads(FileRegistry(), clock=lambda: clock.now)
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('source', download):
            files.retain('source', seconds=180)
        async with files.acquire('source', download) as result:
            clock.now = 180
            await files.expire()
            assert result.file_path.exists()
        assert not result.file_path.exists()
    finally:
        await files.close()


@pytest.mark.asyncio
async def test_timer_removes_retained_original_without_new_requests(tmp_path):
    files = SharedDownloads(FileRegistry())
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('source', download) as result:
            files.retain('source', seconds=.01)
        async with asyncio.timeout(1):
            while result.file_path.exists():
                await asyncio.sleep(.005)
        assert not result.file_path.exists()
    finally:
        await files.close()


@pytest.mark.asyncio
async def test_close_removes_retained_originals_and_registry_holds(tmp_path):
    registry = FileRegistry()
    files = SharedDownloads(registry)
    calls, download = make_download(tmp_path)
    async with files.acquire('source', download) as result:
        files.retain('source', seconds=180)
    await files.close()
    assert not result.file_path.exists() and not registry.snapshot()


@pytest.mark.asyncio
async def test_expired_idle_original_cannot_be_renewed(tmp_path):
    clock = SimpleNamespace(now=0.0)
    files = SharedDownloads(FileRegistry(), clock=lambda: clock.now)
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('source', download) as result:
            files.retain('source', seconds=180)
        clock.now = 180
        assert files.retain('source', seconds=180) is False
        await files.expire()
        assert not result.file_path.exists()
    finally:
        await files.close()


@pytest.mark.asyncio
async def test_failed_reuse_is_not_suppressed_by_retention(tmp_path):
    files = SharedDownloads(FileRegistry())
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('source', download):
            files.retain('source', seconds=180)
        with pytest.raises(RuntimeError, match='upload failed'):
            async with files.acquire('source', download):
                raise RuntimeError('upload failed')
    finally:
        await files.close()


@pytest.mark.asyncio
async def test_expired_active_lease_cannot_remove_replacement(tmp_path):
    clock = SimpleNamespace(now=0.0)
    files = SharedDownloads(FileRegistry(), clock=lambda: clock.now)
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('source', download):
            files.retain('source', seconds=180)
        async with files.acquire('source', download) as old:
            clock.now = 180
            async with files.acquire('source', download) as fresh:
                files.retain('source', seconds=180)
                assert old.file_path != fresh.file_path and old.file_path.exists()
                assert files.retain('source', seconds=180, result=old) is False
        assert not old.file_path.exists() and fresh.file_path.exists()
        async with files.acquire('source', download) as reused:
            assert reused.file_path == fresh.file_path
        assert len(calls) == 2
    finally:
        await files.close()


@pytest.mark.asyncio
async def test_successful_preview_can_renew_its_expired_active_original(tmp_path):
    clock = SimpleNamespace(now=0.0)
    files = SharedDownloads(FileRegistry(), clock=lambda: clock.now)
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('source', download):
            files.retain('source', seconds=180)
        clock.now = 179
        async with files.acquire('source', download) as result:
            clock.now = 180
            await files.expire()
            clock.now = 181
            assert files.retain('source', seconds=180, result=result) is True
        assert result.file_path.exists()
        clock.now = 360.9
        async with files.acquire('source', download) as reused:
            assert reused.file_path == result.file_path
        clock.now = 361
        await files.expire()
        assert not result.file_path.exists() and len(calls) == 1
    finally:
        await files.close()


@pytest.mark.asyncio
async def test_file_after_video_reuses_original_for_three_minutes(setup_runtime):
    state = setup_runtime
    clock = SimpleNamespace(now=0.0)
    state.runtime.files.clock = lambda: clock.now
    await deliver(state, 1, 'preview')
    assert state.created[0].exists()
    clock.now = 179.9
    await deliver(state, 1, 'document')
    state.service.download.assert_awaited_once()
    assert state.bot.send_document.call_args.kwargs['document'].path == state.created[0]
    clock.now = 180
    await state.runtime.files.expire()
    assert not state.created[0].exists()


@pytest.mark.asyncio
async def test_file_after_retention_expiry_downloads_original_again(setup_runtime):
    state = setup_runtime
    clock = SimpleNamespace(now=0.0)
    state.runtime.files.clock = lambda: clock.now
    await deliver(state, 1, 'preview')
    clock.now = 180
    await state.runtime.files.expire()
    await deliver(state, 1, 'document')
    assert state.service.download.await_count == 2
    assert not any(path.exists() for path in state.created)


@pytest.mark.asyncio
async def test_failed_video_upload_does_not_retain_original(setup_runtime):
    state = setup_runtime
    state.bot.send_video.side_effect = RuntimeError('upload failed')
    with pytest.raises(RuntimeError, match='upload failed'):
        await deliver(state, 1, 'preview')
    assert not any(path.exists() for path in state.created)


@pytest.mark.asyncio
async def test_new_cached_video_delivery_renews_existing_original_deadline(setup_runtime):
    state = setup_runtime
    clock = SimpleNamespace(now=0.0)
    state.runtime.files.clock = lambda: clock.now
    await deliver(state, 1, 'preview')
    clock.now = 100
    await deliver(state, 2, 'preview')
    state.service.download.assert_awaited_once()
    clock.now = 180
    await state.runtime.files.expire()
    assert state.created[0].exists()
    clock.now = 280
    await state.runtime.files.expire()
    assert not state.created[0].exists()


@pytest.mark.asyncio
async def test_converted_preview_retains_original_bytes_for_file_delivery(setup_runtime, media_fixture):
    import shutil
    from bot.services.media import verify_media
    state = setup_runtime
    source = await verify_media(media_fixture)
    state.info.formats = [source.model_copy(update={'has_watermark': False})]
    async def download(url, **kwargs):
        path = state.runtime.work_dir / f'tiktok-{len(state.created)}.mp4'
        shutil.copyfile(media_fixture, path)
        state.created.append(path)
        return DownloadResult(file_path=path, file_size_bytes=path.stat().st_size,
                              video_info=state.info, format=source, verified_format=await verify_media(path))
    state.service.download.side_effect = download
    await deliver(state, 1, 'preview')
    original = state.created[0]
    assert original.read_bytes() == media_fixture.read_bytes()
    await deliver(state, 1, 'document')
    state.service.download.assert_awaited_once()
    assert state.bot.send_document.call_args.kwargs['document'].path == original
    assert original.read_bytes() == media_fixture.read_bytes()


async def test_cap_evicts_oldest_eligible_original(tmp_path):
    files = SharedDownloads(FileRegistry(), max_bytes=16)
    calls, download = make_download(tmp_path)
    try:
        results = []
        for key in ['old', 'middle', 'new']:
            async with files.acquire(key, download) as result:
                assert files.retain(key, seconds=180)
                results.append(result)
        assert not results[0].file_path.exists()
        assert results[1].file_path.exists() and results[2].file_path.exists()
        assert sum(p.stat().st_size for p in calls if p.exists()) == 16
    finally:
        await files.close()


async def test_cap_skips_oldest_active_conversion_or_upload(tmp_path):
    files = SharedDownloads(FileRegistry(), max_bytes=16)
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('active', download) as active:
            assert files.retain('active', seconds=180)
            async with files.acquire('idle', download) as idle:
                assert files.retain('idle', seconds=180)
            async with files.acquire('new', download) as newest:
                assert files.retain('new', seconds=180)
            assert active.file_path.exists() and newest.file_path.exists()
            assert not idle.file_path.exists()
    finally:
        await files.close()


async def test_active_over_cap_declines_retention_without_deleting_files(tmp_path):
    files = SharedDownloads(FileRegistry(), max_bytes=8)
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('one', download) as one:
            assert files.retain('one', seconds=180)
            async with files.acquire('two', download) as two:
                assert not files.retain('two', seconds=180)
                assert one.file_path.exists() and two.file_path.exists()
            assert not two.file_path.exists() and one.file_path.exists()
    finally:
        await files.close()


async def test_expiry_and_cap_respect_independent_registry_references(tmp_path):
    clock = SimpleNamespace(now=0.0)
    registry = FileRegistry()
    files = SharedDownloads(registry, max_bytes=16, clock=lambda: clock.now)
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('old', download) as old:
            files.retain('old', seconds=180)
        with registry.track(old.file_path):
            for key in ['middle', 'new']:
                async with files.acquire(key, download):
                    files.retain(key, seconds=180)
            assert old.file_path.exists() and not calls[1].exists() and calls[2].exists()
            clock.now = 180
            await files.expire()
            assert old.file_path.exists()
            assert await FileCleaner(tmp_path, registry, max_age=0).sweep() == 0
        await files.expire()
        assert not old.file_path.exists()
    finally:
        await files.close()


async def test_zero_cap_disables_retention_but_preserves_active_lease(tmp_path):
    files = SharedDownloads(FileRegistry(), max_bytes=0)
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('source', download) as result:
            assert not files.retain('source', seconds=180)
            assert result.file_path.exists()
        assert not result.file_path.exists()
    finally:
        await files.close()


async def test_retention_measures_actual_bytes_and_preserves_oversized_active_file(tmp_path):
    files = SharedDownloads(FileRegistry(), max_bytes=4)
    calls, download = make_download(tmp_path)
    async def inaccurate_metadata():
        return (await download()).model_copy(update={'file_size_bytes': 1})
    try:
        async with files.acquire('source', inaccurate_metadata) as result:
            assert not files.retain('source', seconds=180)
            assert result.file_path.stat().st_size == 8
        assert not result.file_path.exists()
    finally:
        await files.close()


async def test_concurrent_leases_can_exceed_cap_but_are_not_interrupted(tmp_path):
    files = SharedDownloads(FileRegistry(), max_bytes=16)
    calls, download = make_download(tmp_path)
    ready, release = asyncio.Event(), asyncio.Event()
    entered = 0
    async def use(key):
        nonlocal entered
        async with files.acquire(key, download) as result:
            entered += 1
            if entered == 3:
                ready.set()
            await ready.wait()
            files.retain(key, seconds=180)
            await release.wait()
            assert result.file_path.exists()
    tasks = [asyncio.create_task(use(str(index))) for index in range(3)]
    try:
        await asyncio.wait_for(ready.wait(), 1)
        assert sum(path.stat().st_size for path in calls) == 24
        release.set()
        await asyncio.gather(*tasks)
        assert sum(path.stat().st_size for path in calls if path.exists()) <= 16
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await files.close()


async def test_close_defers_deletion_until_active_lease_finishes(tmp_path):
    files = SharedDownloads(FileRegistry(), max_bytes=8)
    calls, download = make_download(tmp_path)
    async with files.acquire('source', download) as result:
        files.retain('source', seconds=180)
        await files.close()
        assert result.file_path.exists()
    assert not result.file_path.exists()


async def test_failed_eviction_declines_new_retention_without_failing_upload(tmp_path, monkeypatch):
    files = SharedDownloads(FileRegistry(), max_bytes=8)
    calls, download = make_download(tmp_path)
    cleanup = DownloadResult.cleanup
    blocked = []
    def unavailable(result):
        if result.file_path in blocked:
            raise PermissionError('Disk temporarily unavailable')
        return cleanup(result)
    monkeypatch.setattr(DownloadResult, 'cleanup', unavailable)
    try:
        async with files.acquire('old', download) as old:
            files.retain('old', seconds=180)
        blocked.append(old.file_path)
        async with files.acquire('new', download) as new:
            assert not files.retain('new', seconds=180)
            assert old.file_path.exists() and new.file_path.exists()
        blocked.clear()
        await files.expire()
        assert sum(p.stat().st_size for p in calls if p.exists()) <= 8
    finally:
        blocked.clear()
        await files.close()


async def test_restart_recovery_cleans_old_retention_without_touching_unrelated_files(tmp_path):
    from bot.config import Settings
    from bot.services.runtime import BotRuntime
    root = tmp_path / 'bot-work'
    root.mkdir()
    stale = root / 'tiktok-retained.mp4'
    stale.write_bytes(b'original')
    unrelated = root / 'keep.txt'
    unrelated.write_text('keep')
    runtime = BotRuntime(Settings(DOWNLOAD_DIR=tmp_path, CACHE_DB_PATH=tmp_path / 'cache.db',
                                  MAX_RETAINED_ORIGINALS_MB=0))
    try:
        await runtime.start()
        assert not stale.exists() and unrelated.exists()
    finally:
        await runtime.close()


async def test_cancelled_download_cleanup_does_not_block_unrelated_acquire(tmp_path):
    files = SharedDownloads(FileRegistry(), max_bytes=8)
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def interrupted():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
    async def use():
        async with files.acquire('cancelled', interrupted):
            pass
    task = asyncio.create_task(use())
    await entered.wait()
    task.cancel()
    await cleaning.wait()
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('unrelated', download) as result:
            assert result.file_path.exists()
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await files.close()


async def test_expired_external_reference_is_cleaned_after_release_without_new_requests(tmp_path):
    registry = FileRegistry()
    files = SharedDownloads(registry, max_bytes=8)
    calls, download = make_download(tmp_path)
    try:
        async with files.acquire('source', download) as result:
            files.retain('source', seconds=.01)
        with registry.track(result.file_path):
            await asyncio.sleep(.03)
            assert result.file_path.exists()
        async with asyncio.timeout(2.5):
            while result.file_path.exists():
                await asyncio.sleep(.02)
    finally:
        await files.close()
