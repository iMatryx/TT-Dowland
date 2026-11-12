"""Original format ranking; no inferred dimensions or resampling."""

from dataclasses import dataclass

from bot.models import VideoFormat
from bot.models.exceptions import VideoUnavailableError


@dataclass(frozen=True)
class FormatSelection:
    video: VideoFormat
    audio: VideoFormat | None = None

    @property
    def known_size_bytes(self) -> int:
        """Exact declared sizes only: a lower bound when streams have missing sizes."""
        return sum(fmt.filesize or 0 for fmt in (self.video, self.audio)
                   if fmt is not None and not fmt.filesize_estimated)

    @property
    def estimated_size(self) -> int | None:
        sizes = [f.filesize for f in (self.video, self.audio) if f is not None]
        return sum(sizes) if all(size is not None for size in sizes) else None


def _quality_key(fmt: VideoFormat) -> tuple[float, ...]:
    clean = 2 if fmt.has_watermark is False else 1 if fmt.has_watermark is None else 0
    short_edge = min(fmt.width, fmt.height) if fmt.width and fmt.height else 0
    area = fmt.width * fmt.height if fmt.width and fmt.height else 0
    return clean, short_edge, area, fmt.fps or 0, fmt.tbr or 0


def select_formats(formats: list[VideoFormat]) -> FormatSelection:
    """Prefer explicit clean sources, then rank native dimensions, FPS and bitrate."""
    videos = [f for f in formats if f.has_video is not False and f.video_codec != "none"]
    if not videos:
        raise VideoUnavailableError("No supported video stream is available.")
    video = max(videos, key=_quality_key)
    audio = None
    if video.audio_codec == "none" or video.has_audio is False:
        audio_formats = [f for f in formats if f.has_video is False and f.has_audio is True]
        if audio_formats:
            audio = max(audio_formats, key=lambda f: f.tbr or 0)
    return FormatSelection(video, audio)
