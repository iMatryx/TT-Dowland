"""Offline regressions for quality-first delivery and safe reuse."""
from bot.models import VideoFormat, VideoInfo
from bot.models.preferences import UserPreferences
from bot.services.cache import CacheEntry, SQLiteCache
from bot.services.quality import select_formats


def test_default_requires_clean_and_then_ranks_quality():
    clean = VideoFormat(format_id='clean', ext='mp4', width=720, height=1280, has_watermark=False)
    high = clean.model_copy(update={'format_id': 'high', 'width': 1080, 'height': 1920, 'has_watermark': True})
    assert select_formats([clean, high]).video == clean
    assert select_formats([clean, high], preference='prefer_clean').video == clean
    for field, value in [('fps', 60), ('tbr', 9000)]:
        better = clean.model_copy(update={field: value, 'format_id': 'better'})
        assert select_formats([clean, better]).video == better
    assert select_formats([clean, clean.model_copy(update={'has_watermark': True})]).video == clean


async def test_new_defaults_preserve_saved_choices(tmp_path):
    store = SQLiteCache(tmp_path / 'cache.db')
    try:
        await store.update_preferences(1, quality='low', watermark='prefer_clean')
        assert await store.get_preferences(2) == UserPreferences(quality='high', watermark='any')
        assert await store.get_preferences(1) == UserPreferences(quality='low', watermark='prefer_clean')
        await store._call(lambda db: db.execute(
            'INSERT INTO user_preferences VALUES (?,?)', (3, '{"quality":"prefer_clean"}')))
        assert (await store.get_preferences(3)).watermark == 'without'
        await store._call(lambda db: db.execute(
            'INSERT INTO user_preferences VALUES (?,?)', (4, '{"quality":"low"}')))
        assert (await store.get_preferences(4)).watermark == 'without'
        assert (await store.update_preferences(4, quality='high')).watermark == 'without'
    finally:
        await store.close()


async def test_preview_can_reuse_only_unmodified_compatible_video_id(tmp_path):
    from bot.config import Settings
    from bot.services.runtime import BotRuntime, quality_key
    runtime = BotRuntime(Settings(DOWNLOAD_DIR=tmp_path, CACHE_DB_PATH=tmp_path / 'cache.db'))
    fmt = VideoFormat(has_watermark=False, format_id='source', ext='mp4', width=64, height=64, video_codec='h264',
                      audio_codec='aac', has_audio=True, pixel_format='yuv420p', container='mov,mp4')
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@u/video/123', formats=[fmt, fmt.model_copy(update={'format_id': 'low', 'width': 32, 'height': 32})])
    options = UserPreferences(watermark='any')
    entry = CacheEntry(bot_id=1, video_id=info.id, url=str(info.webpage_url),
        quality=quality_key(info, 'video', options), kind='video', file_id='original-video',
        size=100, source=fmt, verified=fmt)
    try:
        await runtime.cache.put(entry)
        assert await runtime._cached(1, info, 'preview', options) == entry
        from dataclasses import replace
        for changes in ({'transcoded': True}, {'remuxed': True}, {'video_codec': 'vp9'}):
            await runtime.cache.put(replace(entry, verified=fmt.model_copy(update=changes)))
            assert await runtime._cached(1, info, 'preview', options) is None
        await runtime.cache.put(entry)
        assert await runtime._cached(1, info, 'document', options) is None
        assert await runtime._cached(2, info, 'preview', options) is None
        await runtime.cache.put(replace(entry, processed_at=0))
        assert await runtime._cached(1, info, 'preview', options) is None
        await runtime.cache.put(entry)
        assert await runtime._cached(1, info, 'preview', UserPreferences()) == entry
    finally:
        await runtime.cache.close()
