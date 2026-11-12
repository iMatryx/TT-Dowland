"""Data models for video metadata, formats, and download results."""

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class VideoFormat(BaseModel):
    """A video format reported by a source extractor."""

    format_id: str
    ext: str
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    fps: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    filesize: int | None = Field(default=None, ge=0)
    tbr: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    has_audio: bool | None = None
    has_video: bool | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    has_watermark: bool | None = None
    watermark_evidence: str | None = None
    filesize_estimated: bool = False
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class VideoInfo(BaseModel):
    """Metadata for a publicly available video."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(min_length=1)
    title: str | None = None
    webpage_url: HttpUrl
    uploader: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    formats: list[VideoFormat] = Field(default_factory=list)


class DownloadResult(BaseModel):
    """Result metadata produced after a video is downloaded."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    video_info: VideoInfo
    file_path: Path
    file_size_bytes: int = Field(ge=0)
    format: VideoFormat | None = None
    downloaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verified_format: VideoFormat | None = None
    warnings: list[str] = Field(default_factory=list)

