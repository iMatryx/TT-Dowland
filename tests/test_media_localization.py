"""Captions retain source metadata while translating only interface templates."""
import asyncio
from html import escape
from string import Formatter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.locales.media import MEDIA
from bot.models import DownloadResult, PhotoFile, PhotoInfo, VideoFormat, VideoInfo
from bot.models import exceptions
from bot.services.cache import SQLiteCache
from bot.services.media_info import (
    audio_captions, photo_captions, selection_captions, video_captions,
    video_zip_captions, zip_captions,
)
from bot.utils.i18n import LocalizedText, current_language, language_context
from bot.utils.telegram_text import error_text, video_details


@pytest.fixture
def media(tmp_path):
    # These words deliberately match interface labels and must never be translated.
    info = VideoInfo(id='123', title='Autor: Fotografii <tag>', uploader='necunoscut',
        webpage_url='https://www.tiktok.com/@author/video/123', duration_seconds=12.5)
    fmt = VideoFormat(format_id='probe', ext='mp4', width=1080, height=1920,
        fps=30, tbr=4200, duration_seconds=12.5, video_codec='h264',
        audio_codec='aac', has_audio=True, has_watermark=False)
    photo_info = info.model_copy(update={'media_type': 'photo', 'photos': [
        PhotoInfo(urls=['https://cdn.example/photo.jpg'], width=1080, height=1920,
                  has_watermark=False)]})
    path = tmp_path / 'photo.jpg'
    path.write_bytes(b'image')
    photo = PhotoFile(file_path=path, width=1080, height=1920, size=5)
    result = DownloadResult(video_info=photo_info, file_path=tmp_path / 'photos.zip',
                            file_size_bytes=12345, photos=[photo])
    return info, fmt, photo_info, photo, result


def test_every_media_template_preserves_interpolation_fields():
    def fields(template):
        return sorted((field, spec, conversion) for _, field, spec, conversion
                      in Formatter().parse(template) if field is not None)
    for source, translated in MEDIA.items():
        assert set(translated) == {'ru', 'en'}
        for template in translated.values():
            assert template
            assert fields(template) == fields(source), source


@pytest.mark.parametrize('language,word', [('ro', 'Rezoluție'), ('ru', 'Разрешение'), ('en', 'Resolution')])
def test_caption_factories_render_localized_variants_without_mutating_metadata(media, language, word):
    info, fmt, photo_info, photo, result = media
    with language_context(language):
        captions = [video_captions(info, fmt, fmt, 12345),
                    audio_captions(info, fmt, 12345),
                    photo_captions(photo_info, photo, 0, preview=True),
                    zip_captions(result), video_zip_captions(info, fmt, fmt, 12345),
                    selection_captions(info)]
        details = video_details(info, fmt, fmt, 12345)
        assert word in details
        for summary, expanded in captions:
            for text in (summary, expanded):
                assert isinstance(text, LocalizedText)
                assert set(text.translations) == {'ro', 'ru', 'en'}
                assert str(text) == text.translations[language]
                assert escape(info.title) in text
                assert '<tag>' not in text
                assert len(text) <= 1024
            assert '👤' in expanded
            assert 'necunoscut' in expanded  # Actual author, not the unknown-author fallback.
        assert '1080 × 1920' in details
        assert info.title == 'Autor: Fotografii <tag>' and info.uploader == 'necunoscut'


@pytest.mark.parametrize('language', ['ru', 'en'])
def test_each_domain_error_is_localized_without_exposing_raw_exception(language):
    error_classes = [exceptions.SourceVariantUnavailableError, exceptions.QueueFullError,
        exceptions.InvalidURLError, exceptions.FileSizeLimitError, exceptions.DeletedVideoError,
        exceptions.PrivateVideoError, exceptions.RegionalRestrictionError, exceptions.DownloadTimeoutError,
        exceptions.NetworkError, exceptions.CorruptFileError, exceptions.InsufficientSpaceError,
        exceptions.MissingFFmpegError, exceptions.TelegramDeliveryError, exceptions.VideoUnavailableError,
        RuntimeError]
    for kind in error_classes:
        exc = kind('secret-token https://private.example')
        with language_context('ro'):
            romanian = error_text(exc)
        with language_context(language):
            translated = error_text(exc)
        assert translated != romanian
        assert translated.startswith('❌ ')
        assert 'secret-token' not in translated and 'https://' not in translated
    with language_context(language):
        rate_message = error_text(exceptions.RateLimitedError(17))
    assert '17' in rate_message
    assert 'Ai atins' not in rate_message


async def test_simultaneous_caption_rendering_keeps_locale_isolated(media):
    info, fmt, *_ = media
    async def render(language):
        with language_context(language):
            await asyncio.sleep(0)
            text = video_captions(info, fmt, fmt, 12345)[1]
            await asyncio.sleep(0)
            assert current_language() == language
            return str(text)
    ru, en, ro = await asyncio.gather(render('ru'), render('en'), render('ro'))
    assert 'Размер файла' in ru and 'File size' in en and 'Dimensiune fișier' in ro
    assert current_language() == 'ro'


async def test_saved_info_uses_new_language_without_translating_metadata(media, tmp_path):
    info, fmt, *_ = media
    store = SQLiteCache(tmp_path / 'captions.sqlite3')
    try:
        with language_context('ru'):
            await store.put_media_details(1, 10, 20, *video_captions(info, fmt, fmt, 12345))
        with language_context('en'):
            short, details = await store.get_media_details(1, 10, 20)
            assert 'File size' in details and 'Размер файла' not in details
            assert short == '<b>Autor: Fotografii &lt;tag&gt;</b>'
            assert '👤 Author: necunoscut' in details
    finally:
        await store.close()


@pytest.mark.parametrize('language,expected', [('ru', 'Недействительная кнопка.'), ('en', 'Invalid button.')])
async def test_callback_errors_follow_user_locale(language, expected):
    from bot.handlers.media_info import pending_info_handler
    query = SimpleNamespace(data='broken', answer=AsyncMock())
    with language_context(language):
        await pending_info_handler(query, None, None, None)
    query.answer.assert_awaited_once_with(expected, show_alert=True)
