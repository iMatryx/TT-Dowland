"""Offline regressions for quality-first delivery and safe reuse."""
from bot.models import VideoFormat, VideoInfo
from bot.services.cache import CacheEntry, SQLiteCache
from bot.services.quality import select_formats


def test_default_requires_clean_and_then_ranks_quality():
    clean = VideoFormat(format_id='clean', ext='mp4', width=720, height=1280, has_watermark=False)
    high = clean.model_copy(update={'format_id': 'high', 'width': 1080, 'height': 1920, 'has_watermark': True})
    assert select_formats([clean, high]).video == clean
    assert select_formats([clean, high]).video == clean
    for field, value in [('fps', 60), ('tbr', 9000)]:
        better = clean.model_copy(update={field: value, 'format_id': 'better'})
        assert select_formats([clean, better]).video == better
    assert select_formats([clean, clean.model_copy(update={'has_watermark': True})]).video == clean


async def test_preview_can_reuse_only_unmodified_compatible_video_id(tmp_path):
    from bot.config import Settings
    from bot.services.runtime import BotRuntime, quality_key
    runtime = BotRuntime(Settings(DOWNLOAD_DIR=tmp_path, CACHE_DB_PATH=tmp_path / 'cache.db'))
    fmt = VideoFormat(has_watermark=False, format_id='source', ext='mp4', width=64, height=64, video_codec='h264',
                      audio_codec='aac', has_audio=True, pixel_format='yuv420p', container='mov,mp4')
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@u/video/123', formats=[fmt, fmt.model_copy(update={'format_id': 'low', 'width': 32, 'height': 32})])
    entry = CacheEntry(bot_id=1, video_id=info.id, url=str(info.webpage_url),
        quality=quality_key(info, 'video'), kind='video', file_id='original-video',
        size=100, source=fmt, verified=fmt)
    try:
        await runtime.cache.put(entry)
        assert await runtime._cached(1, info, 'preview') == entry
        from dataclasses import replace
        for changes in ({'transcoded': True}, {'remuxed': True}, {'video_codec': 'vp9'}):
            await runtime.cache.put(replace(entry, verified=fmt.model_copy(update=changes)))
            assert await runtime._cached(1, info, 'preview') is None
        await runtime.cache.put(entry)
        assert await runtime._cached(1, info, 'document') is None
        assert await runtime._cached(2, info, 'preview') is None
        await runtime.cache.put(replace(entry, processed_at=0))
        assert await runtime._cached(1, info, 'preview') is None
        await runtime.cache.put(entry)
        assert await runtime._cached(1, info, 'preview') == entry
    finally:
        await runtime.cache.close()
