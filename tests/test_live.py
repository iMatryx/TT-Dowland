"""Explicit opt-in public downloads; Telegram sends require another opt-in."""

import os
import zipfile
import pytest

from bot.config import Settings
from bot.services.downloader import TikTokDownloader
from bot.services.telegram_download import TelegramDownloadService
from bot.services.media import verify_media
from bot.utils.performance import platform_for
from bot.services.urls import normalize_url


@pytest.mark.live
@pytest.mark.parametrize('platform,variable,kind', [
    ('tiktok', 'TIKTOK_TEST_URL', 'video'), ('tiktok', 'TIKTOK_TEST_PHOTO_URL', 'photo'),
    ('youtube', 'YOUTUBE_SHORTS_TEST_URL', 'video'), ('instagram', 'INSTAGRAM_REELS_TEST_URL', 'video'),
], ids=['tiktok-video', 'tiktok-photo', 'youtube-shorts', 'instagram-reels'])
async def test_public_media_live(tmp_path, platform, variable, kind):
    if os.environ.get('RUN_PLATFORM_LIVE') != '1':
        pytest.skip('Platform live check not enabled: set RUN_PLATFORM_LIVE=1 on the server')
    if os.environ.get('LIVE_TEST_CONTEXT') != 'server':
        pytest.skip('Server context not declared: set LIVE_TEST_CONTEXT=server only on the server')
    url = os.environ.get(variable)
    if not url:
        pytest.skip(f'Unverified {platform}/{kind}: missing {variable}')
    assert platform_for(normalize_url(url)) == platform, f'{variable} must belong to the selected platform'
    engine = TikTokDownloader(Settings(DOWNLOAD_DIR=tmp_path, BOT_TOKEN=None, VERIFY_FULL_DECODE=False))
    service = TelegramDownloadService(engine)
    result = None
    try:
        info = await service.analyze(url)
        assert info.id and info.media_type == kind
        assert info.photos if kind == 'photo' else info.formats
        result = await service.download(url)
        assert result.video_info.id == info.id and result.video_info.media_type == kind
        assert result.file_path.is_file() and result.file_path.stat().st_size == result.file_size_bytes > 0
        if kind == 'photo':
            assert result.photos and len(result.photos) == len(info.photos)
            for photo in result.photos:
                verified = await verify_media(photo.file_path, full_decode=True)
                assert verified.width == photo.width and verified.height == photo.height
                assert photo.file_path.stat().st_size == photo.size > 0
            with zipfile.ZipFile(result.file_path) as archive:
                assert archive.testzip() is None
                assert archive.namelist() == [photo.file_path.name for photo in result.photos]
        else:
            verified = await verify_media(result.file_path, full_decode=True)
            assert verified.has_video and verified.width > 0 and verified.height > 0
            assert verified.duration_seconds is not None and verified.duration_seconds > 0
            assert result.verified_format.width == verified.width
            assert result.verified_format.height == verified.height
        print(f'LIVE_RESULT platform={platform} kind={kind} analysis=passed download=passed integrity=passed')
    finally:
        if result is not None:
            result.cleanup()
        await engine.close()


@pytest.mark.live
async def test_real_telegram_delivery(tmp_path):
    if os.environ.get('RUN_TELEGRAM_LIVE') != '1':
        pytest.skip('Set RUN_TELEGRAM_LIVE=1 and TELEGRAM_TEST_CHAT_ID to send real Telegram test messages')
    chat = os.environ.get('TELEGRAM_TEST_CHAT_ID')
    urls = [os.environ.get('TIKTOK_TEST_URL'), os.environ.get('TIKTOK_TEST_PHOTO_URL')]
    if not chat or not all(urls):
        pytest.skip('Configure both public TikTok links and TELEGRAM_TEST_CHAT_ID')
    from bot.telegram_config import create_bot, create_dispatcher
    settings = Settings(DOWNLOAD_DIR=tmp_path)
    bot = create_bot(settings)
    dispatcher = create_dispatcher(settings)
    sender = dispatcher['sender']
    engine = dispatcher['download_service'].engine
    try:
        for url in urls:
            result = await engine.download(url)
            try:
                modes = ['album', 'photos', 'zip'] if result.photos else ['video', 'document']
                for mode in modes:
                    await sender.send(bot, int(chat), result, mode)
            finally:
                result.cleanup()
    finally:
        await engine.close()
        await dispatcher['preferences'].close()
        await bot.session.close()
