"""The public delivery policy has no low-quality or watermark fallback."""
import pytest

from bot.models import VideoFormat
from bot.models.exceptions import SourceVariantUnavailableError
from bot.services.quality import select_formats, select_audio


def test_default_selects_highest_clean_source():
    formats = [VideoFormat(format_id='clean-small', ext='mp4', width=360, height=640, has_watermark=False),
        VideoFormat(format_id='clean-best', ext='mp4', width=1080, height=1920, has_watermark=False),
        VideoFormat(format_id='marked', ext='mp4', width=2160, height=3840, has_watermark=True)]
    assert select_formats(formats).video.format_id == 'clean-best'


@pytest.mark.parametrize('status', [True, None])
def test_no_fallback_to_watermarked_or_unknown_source(status):
    with pytest.raises(SourceVariantUnavailableError):
        select_formats([VideoFormat(format_id='source', ext='mp4', has_watermark=status)])


def test_low_selection_is_no_longer_supported():
    with pytest.raises(TypeError):
        select_audio([VideoFormat(format_id='source', ext='mp3', has_audio=True)], quality='low')


@pytest.mark.parametrize('watermark', ['any', 'prefer_clean', 'watermarked'])
def test_selector_rejects_removed_watermark_options(watermark):
    with pytest.raises(TypeError):
        select_formats([VideoFormat(format_id='clean', ext='mp4', has_watermark=False)], watermark=watermark)
