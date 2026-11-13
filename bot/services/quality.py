"""Original format ranking; no inferred dimensions or resampling."""

from dataclasses import dataclass

from bot.models import VideoFormat
from bot.models.exceptions import VideoUnavailableError, SourceVariantUnavailableError


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


def select_formats(formats: list[VideoFormat], *, preference: str = "clean",
                   quality: str = "high", watermark: str | None = None) -> FormatSelection:
    """Choose the highest source variant identified without watermark."""
    videos = [f for f in formats if f.has_video is not False and f.video_codec != "none"]
    if quality != "high":
        raise ValueError("Only highest source quality is supported")
    if watermark not in {None, "without"}:
        raise ValueError("Only sources identified without watermark are supported")
    videos = [f for f in videos if f.has_watermark is False]
    if not videos:
        raise SourceVariantUnavailableError("No source variant identified without watermark is available.")
    video = max(videos, key=lambda f: _quality_key(f)[1:] + (_quality_key(f)[0],))
    audio = None
    if video.audio_codec == "none" or video.has_audio is False:
        audio_formats = [f for f in formats if f.has_video is False and f.has_audio is True]
        if audio_formats:
            audio = max(audio_formats, key=lambda f: f.tbr or 0)
    return FormatSelection(video, audio)


def select_photos(info, *, quality='high', watermark='without'):
    """Select an existing image variant at each original album position."""
    from bot.models.video import PhotoInfo
    selected = []
    for photo in info.photos:
        variants = photo.variants or [photo]
        formats = [VideoFormat(format_id=str(i), ext='image', width=p.width, height=p.height,
                               has_watermark=p.has_watermark) for i, p in enumerate(variants)]
        choice = select_formats(formats, quality=quality, watermark=watermark).video
        variant = variants[int(choice.format_id)]
        selected.append(PhotoInfo.model_validate({**variant.model_dump(exclude={'variants'}),
                                                 'variants': photo.variants}))
    return info.model_copy(update={'photos': selected})


def select_audio(formats: list[VideoFormat], *, quality: str = 'high') -> VideoFormat:
    """Prefer an audio-only source; fall back to extracting a muxed soundtrack."""
    if quality != 'high':
        raise ValueError('Unknown quality')
    candidates = [f for f in formats if f.has_audio is not False and f.audio_codec != 'none']
    if not candidates:
        raise VideoUnavailableError('No audio stream is available.')
    standalone = [f for f in candidates if f.has_video is False or f.video_codec == 'none']
    candidates = standalone or candidates
    known = [f for f in candidates if f.has_audio is True or f.audio_codec]
    candidates = known or candidates
    return max(candidates, key=lambda f: f.tbr or 0)
