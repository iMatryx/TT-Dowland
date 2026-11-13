"""Data models for video metadata, formats, and download results."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, PrivateAttr, model_validator


class VideoFormat(BaseModel):
    """A video format reported by a source extractor."""

    # Process-local proof only; never persisted as source or Telegram metadata.
    _file_identity: tuple[int, ...] | None = PrivateAttr(default=None)
    _verified_data: str | None = PrivateAttr(default=None)

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
    pixel_format: str | None = None
    container: str | None = None
    remuxed: bool = False
    transcoded: bool = False


class PhotoVariant(BaseModel):
    """One source image; URLs are mirrors of the same selected variant."""

    urls: list[HttpUrl] = Field(min_length=1, repr=False)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    has_watermark: bool | None = None
    watermark_evidence: str | None = None


class PhotoInfo(PhotoVariant):
    variants: list[PhotoVariant] = Field(default_factory=list, repr=False)


class PhotoFile(BaseModel):
    file_path: Path
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    size: int = Field(gt=0)


class VideoInfo(BaseModel):
    """Metadata for a publicly available video."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(min_length=1)
    title: str | None = None
    webpage_url: HttpUrl
    uploader: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    formats: list[VideoFormat] = Field(default_factory=list)
    media_type: Literal["video", "photo"] = "video"
    photos: list[PhotoInfo] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_media(self):
        if self.media_type == "photo" and not self.photos:
            raise ValueError("Photo posts must contain images")
        if self.media_type == "video" and self.photos:
            raise ValueError("Photo posts must declare their media type")
        return self


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
    photos: list[PhotoFile] = Field(default_factory=list)
    audio_only: bool = False

    def cleanup(self) -> None:
        """Release published artifacts after the last consumer finishes."""
        self.file_path.unlink(missing_ok=True)
        for photo in self.photos:
            photo.file_path.unlink(missing_ok=True)
        if self.photos:
            self.file_path.parent.rmdir()

