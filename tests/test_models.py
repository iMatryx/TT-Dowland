"""Tests for domain models."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from bot.models.video import DownloadResult, VideoFormat, VideoInfo


def test_video_models_accept_valid_data() -> None:
    """Video models preserve valid metadata and nested formats."""
    video_format = VideoFormat(
        format_id="best",
        ext="mp4",
        width=1080,
        height=1920,
        has_audio=True,
    )
    video_info = VideoInfo(
        id="abc123",
        title="Sample video",
        webpage_url="https://www.tiktok.com/@user/video/abc123",
        uploader="user",
        formats=[video_format],
    )
    result = DownloadResult(
        video_info=video_info,
        file_path=Path("downloads/abc123.mp4"),
        file_size_bytes=1024,
        format=video_format,
        downloaded_at=datetime.now(timezone.utc),
    )

    assert result.video_info.id == "abc123"
    assert result.format is not None
    assert result.format.height == 1920


def test_video_format_rejects_negative_dimensions() -> None:
    """Invalid dimensions are rejected by validation."""
    with pytest.raises(ValidationError):
        VideoFormat(format_id="bad", ext="mp4", height=-1)
