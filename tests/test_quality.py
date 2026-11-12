import pytest

from bot.models import VideoFormat
from bot.services.quality import select_formats
from bot.models.exceptions import VideoUnavailableError


def fmt(name: str, width: int | None, height: int | None, **kw: object) -> VideoFormat:
    return VideoFormat(format_id=name, ext="mp4", width=width, height=height, **kw)


def test_picks_real_4k_for_vertical_or_landscape() -> None:
    formats = [fmt("720", 720, 1280), fmt("1080", 1080, 1920),
               fmt("1440", 2560, 1440), fmt("4k", 2160, 3840)]
    assert select_formats(formats).video.format_id == "4k"


def test_no_upscaling_or_invented_resolution() -> None:
    source = fmt("source", None, None)
    selected = select_formats([source])
    assert selected.video.width is None
    assert selected.video.height is None
    assert select_formats([fmt("720", 1280, 720)]).video.height == 720


def test_known_clean_precedes_unknown_and_watermarked() -> None:
    clean = fmt("clean", 720, 1280, has_watermark=False)
    unknown = fmt("unknown", 1080, 1920)
    watermarked = fmt("wm", 2160, 3840, has_watermark=True)
    assert select_formats([watermarked, unknown, clean]).video == clean
    assert select_formats([watermarked, unknown]).video == unknown
    assert select_formats([watermarked]).video.has_watermark is True


def test_quality_ties_use_fps_then_bitrate() -> None:
    low = fmt("low", 1080, 1920, fps=30, tbr=9000)
    high = fmt("high", 1080, 1920, fps=60, tbr=5000)
    assert select_formats([low, high]).video == high


def test_video_only_selects_separate_audio() -> None:
    video = fmt("video", 1080, 1920, audio_codec="none", has_audio=False)
    audio = fmt("audio", None, None, video_codec="none", has_video=False,
                audio_codec="aac", has_audio=True, tbr=128)
    selection = select_formats([audio, video])
    assert selection.video == video
    assert selection.audio == audio


def test_audio_only_is_not_a_video() -> None:
    audio = fmt("audio", None, None, has_video=False, video_codec="none")
    with pytest.raises(VideoUnavailableError):
        select_formats([audio])
