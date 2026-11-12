from pathlib import Path

import pytest

from bot.models import VideoFormat
from bot.services.cache import CacheEntry, SQLiteCache


@pytest.mark.asyncio
async def test_cache_persists_and_keeps_original_separate(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite3"
    fmt = VideoFormat(format_id="source", ext="mp4", width=1080, height=1920)
    entry = CacheEntry(bot_id=1, video_id="123", url="https://www.tiktok.com/@u/video/123",
                       quality="hd", kind="video", file_id="video-file", size=10,
                       source=fmt, verified=fmt)
    cache = SQLiteCache(path)
    await cache.put(entry)
    await cache.close()
    reopened = SQLiteCache(path)
    assert (await reopened.get(1, "123", "hd", "video")).file_id == "video-file"
    assert await reopened.get(1, "123", "hd", "document") is None
    assert await reopened.get(2, "123", "hd", "video") is None
    await reopened.close()


@pytest.mark.asyncio
async def test_expired_and_non_original_documents_are_not_reused(tmp_path: Path) -> None:
    import time

    cache = SQLiteCache(tmp_path / "cache.sqlite3", ttl=10)
    try:
        entry = CacheEntry(bot_id=1, video_id="123", url="https://www.tiktok.com/@u/video/123",
                           quality="hd", kind="document", file_id="id", size=10,
                           source=None, verified=None, original=False)
        await cache.put(entry)
        assert await cache.get(1, "123", "hd", "document") is None
        from dataclasses import replace
        await cache.put(replace(entry, original=True, processed_at=time.time() - 20))
        assert await cache.get(1, "123", "hd", "document") is None
    finally:
        await cache.close()
