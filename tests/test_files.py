import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.models import DownloadResult, VideoInfo
from bot.services.files import FileRegistry, FileCleaner, SharedDownloads


@pytest.mark.asyncio
async def test_cleanup_leaves_active_recent_and_unrelated_files(tmp_path: Path) -> None:
    registry = FileRegistry()
    old = tmp_path / ".tiktok-old"
    old.mkdir()
    (old / "part").write_bytes(b"partial")
    active = tmp_path / ".analysis-active"
    active.mkdir()
    recent = tmp_path / "tiktok-recent.mp4"
    recent.touch()
    unrelated = tmp_path / "keep.txt"
    unrelated.touch()
    os.utime(old, (1, 1))
    os.utime(active, (1, 1))
    cleaner = FileCleaner(tmp_path, registry, max_age=60)
    with registry.track(active):
        assert await cleaner.sweep() == 1
        assert active.exists()
    assert recent.exists() and unrelated.exists()
    assert not old.exists()


@pytest.mark.asyncio
async def test_shared_download_different_modes_retain_file_until_last_user(tmp_path: Path) -> None:
    files = SharedDownloads(FileRegistry())
    path = tmp_path / "original.mp4"
    started, release, second_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def download():
        nonlocal calls
        calls += 1
        path.write_bytes(b"original")
        return DownloadResult(file_path=path, file_size_bytes=8, video_info=VideoInfo(id="123", webpage_url="https://www.tiktok.com/@author/video/123"))

    async def first():
        async with files.acquire("123", download):
            started.set()
            await release.wait()
            assert path.exists()

    async def second():
        await started.wait()
        async with files.acquire("123", download):
            assert path.exists()
        second_done.set()

    first_task, second_task = asyncio.create_task(first()), asyncio.create_task(second())
    await second_done.wait()
    assert path.exists()
    release.set()
    await asyncio.gather(first_task, second_task)
    assert calls == 1
    assert not path.exists()
    await files.close()


async def test_cleanup_rechecks_references_after_dispatch_to_thread(tmp_path, monkeypatch):
    from bot.services import files as module
    registry = FileRegistry()
    path = tmp_path / 'tiktok-active.mp4'
    path.write_bytes(b'original')
    os.utime(path, (1, 1))
    original = asyncio.to_thread
    async def with_new_reference(function, *args, **kwargs):
        with registry.track(path):
            return await original(function, *args, **kwargs)
    monkeypatch.setattr(module.asyncio, 'to_thread', with_new_reference)
    assert await FileCleaner(tmp_path, registry).sweep(recovery=True) == 0
    assert path.read_bytes() == b'original'
