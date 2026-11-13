"""The public delivery policy has no low-quality or watermark fallback."""
import pytest

from bot.models import VideoFormat
from bot.models.preferences import UserPreferences
from bot.models.exceptions import SourceVariantUnavailableError
from bot.services.quality import select_formats, select_audio


@pytest.mark.parametrize('saved', [{}, {'quality': 'low', 'watermark': 'any'},
    {'quality': 'maximum'}, {'quality': 'prefer_clean'}, {'watermark': 'watermarked'}])
def test_old_settings_use_fixed_policy(saved):
    assert UserPreferences.model_validate(saved).model_dump() == {'quality': 'high', 'watermark': 'without'}


def test_menu_has_no_source_quality_controls():
    from bot.handlers.keyboards import preferences_keyboard
    keyboard = preferences_keyboard(1, UserPreferences())
    values = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert not any(':quality:' in value or ':watermark:' in value for value in values)


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
    with pytest.raises(ValueError):
        select_audio([VideoFormat(format_id='source', ext='mp3', has_audio=True)], quality='low')


@pytest.mark.parametrize('watermark', ['any', 'prefer_clean', 'watermarked'])
def test_selector_rejects_removed_watermark_options(watermark):
    with pytest.raises(ValueError):
        select_formats([VideoFormat(format_id='clean', ext='mp4', has_watermark=False)], watermark=watermark)
