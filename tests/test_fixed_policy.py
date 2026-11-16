"""Prefer clean sources, allowing unknown watermark status as a fallback."""
import pytest

from bot.models import VideoFormat
from bot.models.exceptions import SourceVariantUnavailableError
from bot.services.quality import select_formats, select_audio


def test_default_selects_highest_clean_source():
    formats = [VideoFormat(format_id='clean-small', ext='mp4', width=360, height=640, has_watermark=False),
        VideoFormat(format_id='clean-best', ext='mp4', width=1080, height=1920, has_watermark=False),
        VideoFormat(format_id='marked', ext='mp4', width=2160, height=3840, has_watermark=True)]
    assert select_formats(formats).video.format_id == 'clean-best'


def test_no_fallback_to_known_watermarked_source():
    with pytest.raises(SourceVariantUnavailableError):
        select_formats([VideoFormat(format_id='source', ext='mp4', has_watermark=True)])


def test_unknown_watermark_fallback_selects_highest_quality_and_separate_audio():
    formats = [VideoFormat(format_id='unknown-small', ext='mp4', width=360, height=640),
        VideoFormat(format_id='unknown-best', ext='mp4', width=1080, height=1920, has_audio=False),
        VideoFormat(format_id='marked', ext='mp4', width=2160, height=3840, has_watermark=True),
        VideoFormat(format_id='audio', ext='m4a', has_video=False, has_audio=True)]
    selection = select_formats(formats)
    assert selection.video.format_id == 'unknown-best'
    assert selection.video.has_watermark is None
    assert selection.audio.format_id == 'audio'


def test_low_selection_is_no_longer_supported():
    with pytest.raises(TypeError):
        select_audio([VideoFormat(format_id='source', ext='mp3', has_audio=True)], quality='low')


@pytest.mark.parametrize('watermark', ['any', 'prefer_clean', 'watermarked'])
def test_selector_rejects_removed_watermark_options(watermark):
    with pytest.raises(TypeError):
        select_formats([VideoFormat(format_id='clean', ext='mp4', has_watermark=False)], watermark=watermark)
