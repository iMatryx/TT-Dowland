import pytest
from bot.models import VideoFormat
from bot.services.quality import select_formats


def variants():
    return [VideoFormat(format_id='clean-high', ext='mp4', width=1080, height=1920, has_watermark=False),
            VideoFormat(format_id='clean-low', ext='mp4', width=360, height=640, has_watermark=False),
            VideoFormat(format_id='wm-high', ext='mp4', width=2160, height=3840, has_watermark=True),
            VideoFormat(format_id='wm-low', ext='mp4', width=720, height=1280, has_watermark=True)]


def test_only_highest_clean_native_variant_is_selected():
    assert select_formats(variants()).video.format_id == 'clean-high'
    for quality in ('low', 'bogus'):
        with pytest.raises(ValueError):
            select_formats(variants(), quality=quality)


def test_unknown_resolution_does_not_displace_known_clean_resolution():
    formats = variants()[:2] + [VideoFormat(format_id='unknown', ext='mp4', has_watermark=False)]
    assert select_formats(formats).video.format_id == 'clean-high'


def test_watermarked_or_unknown_source_is_unavailable():
    from bot.models.exceptions import SourceVariantUnavailableError
    with pytest.raises(SourceVariantUnavailableError):
        select_formats(variants()[2:])


def test_legacy_preferences_migrate_to_independent_settings():
    from bot.models.preferences import UserPreferences
    old = UserPreferences.model_validate({'quality': 'prefer_clean', 'video_as_document': True,
                                          'photo_mode': 'zip', 'ask_each_time': False})
    assert old.quality == 'high' and old.watermark == 'without'
    assert old.model_dump() == {'quality': 'high', 'watermark': 'without'}
    assert UserPreferences.model_validate({'quality': 'maximum'}).watermark == 'without'


def test_preferences_keyboard_has_no_delivery_or_auto_controls():
    from bot.handlers.keyboards import preferences_keyboard, download_keyboard
    from bot.models.preferences import UserPreferences
    from bot.models import VideoInfo
    keys = preferences_keyboard(11, UserPreferences())
    values = [b.callback_data for row in keys.inline_keyboard for b in row]
    assert not any(':quality:' in v for v in values)
    assert not any(':watermark:' in v for v in values)
    assert not any('ask_each_time' in v or 'photo_mode' in v or 'video_as_document' in v for v in values)
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@a/video/123', formats=variants())
    keyboard = download_keyboard('key', info)
    modes = [b.callback_data.split(':')[1] for row in keyboard.inline_keyboard for b in row if b.callback_data.startswith('dl:')]
    assert modes == ['chat', 'file', 'zip', 'audio']


def test_photo_variants_preserve_order_and_independent_options():
    from bot.services.tiktok_sources import photo_metadata
    from bot.services.quality import select_photos
    from bot.services.extraction import metadata_from_ytdlp
    item = {'id': '123', 'imagePost': {'images': [
        {'originImage': {'urlList': [f'https://cdn.example/{i}-high'], 'width': 2000, 'height': 3000},
         'displayImage': {'urlList': [f'https://cdn.example/{i}-low'], 'width': 600, 'height': 900},
         'ownerWatermarkImage': {'urlList': [f'https://cdn.example/{i}-wm'], 'width': 1000, 'height': 1500}}
        for i in range(2)]}}
    info = metadata_from_ytdlp(photo_metadata(item, 'https://www.tiktok.com/@a/photo/123'), 'https://www.tiktok.com/@a/photo/123')
    for photo in info.photos:
        for variant in photo.variants:
            if variant.has_watermark is None:
                variant.has_watermark = False
    selected = select_photos(info)
    assert [str(p.urls[0]) for p in selected.photos] == ['https://cdn.example/0-high', 'https://cdn.example/1-high']



@pytest.mark.asyncio
async def test_video_zip_keeps_original_bytes_and_has_separate_receipt(tmp_path):
    import zipfile
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from bot.models import VideoInfo, DownloadResult
    from bot.services.telegram_sender import TelegramSender
    path = tmp_path / 'original.mp4'
    path.write_bytes(b'original-video-bytes')
    info = VideoInfo(id='123', webpage_url='https://www.tiktok.com/@a/video/123')
    result = DownloadResult(video_info=info, file_path=path, file_size_bytes=20)
    sent_paths = []

    async def send(**kwargs):
        archive = kwargs['document'].path
        sent_paths.append(archive)
        with zipfile.ZipFile(archive) as content:
            assert content.namelist() == ['video.mp4']
            assert content.read('video.mp4') == b'original-video-bytes'
            assert content.getinfo('video.mp4').compress_type == zipfile.ZIP_STORED
        return SimpleNamespace(document=SimpleNamespace(file_id='zip-id'))

    bot = SimpleNamespace(send_document=AsyncMock(side_effect=send))
    result.file_size_bytes = path.stat().st_size
    receipt = await TelegramSender().send_with_receipt(bot, 1, result, 'zip')
    assert receipt.kind == 'zip' and receipt.file_id == 'zip-id'
    assert receipt.file_size_bytes > result.file_size_bytes
    assert not sent_paths[0].exists() and path.exists()
