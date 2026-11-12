"""Extractor protocol and the real, isolated yt-dlp implementation."""

from dataclasses import dataclass
import asyncio
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Protocol

from pydantic import ValidationError

from bot.models import VideoFormat, VideoInfo
from bot.models.exceptions import VideoUnavailableError, classify_error
from bot.utils.process import run_process


@dataclass(frozen=True)
class Extraction:
    video: VideoInfo
    info_path: Path


class Extractor(Protocol):
    """Alternative compliant extractors implement this async metadata boundary."""

    async def extract(self, url: str, workspace: Path) -> Extraction: ...


def _number(value: Any, *, integer: bool = False, positive: bool = False) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if not math.isfinite(value) or value < 0 or (positive and value == 0):
            return None
    except OverflowError:
        return None
    if integer:
        return int(value) if value == int(value) else None
    return value


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def metadata_from_ytdlp(info: dict[str, Any], url: str) -> VideoInfo:
    """Copy reported fields, preserving missing values and watermark evidence."""
    if info.get("_type", "video") != "video" or info.get("is_live"):
        raise VideoUnavailableError("Only individual, non-live public videos are supported.")
    if info.get("availability") not in {None, "public", "unlisted"}:
        raise classify_error("private or login required")
    if not info.get("id"):
        raise VideoUnavailableError("Extractor did not report a video ID.")
    formats = []
    for source in info.get("formats") or []:
        if not isinstance(source, dict) or not source.get("format_id") or not source.get("url"):
            continue
        if source.get("has_drm") or "UNPLAYABLE" in str(source.get("format_note", "")):
            continue
        if source.get("protocol", "https") not in {"http", "https"}:
            continue
        note = _text(source.get("format_note")) or ""
        watermark, evidence = None, None
        if isinstance(source.get("has_watermark"), bool):
            watermark, evidence = source["has_watermark"], "extractor.has_watermark"
        elif re.search(r"\b(no[ -]watermark|without watermark|unwatermarked|not watermarked)\b", note, re.I):
            watermark, evidence = False, f"extractor.format_note: {note}"
        elif re.search(r"\bwatermarked\b", note, re.I):
            watermark, evidence = True, f"extractor.format_note: {note}"
        width = _number(source.get("width"), integer=True, positive=True)
        height = _number(source.get("height"), integer=True, positive=True)
        vcodec, acodec = _text(source.get("vcodec")), _text(source.get("acodec"))
        exact_size = _number(source.get("filesize"), integer=True)
        approximate = _number(source.get("filesize_approx"), integer=True)
        formats.append(VideoFormat(
            format_id=str(source["format_id"]), ext=_text(source.get("ext")) or "unknown",
            width=width, height=height,
            fps=_number(source.get("fps"), positive=True),
            filesize=exact_size if exact_size is not None else approximate,
            filesize_estimated=exact_size is None and approximate is not None,
            video_codec=vcodec, audio_codec=acodec,
            has_video=False if vcodec == "none" else True if vcodec or width or height else None,
            has_audio=False if acodec == "none" else True if acodec else None,
            tbr=_number(source.get("tbr")),
            has_watermark=watermark, watermark_evidence=evidence,
        ))
    try:
        return VideoInfo(
            id=str(info["id"]), webpage_url=url,
            title=_text(info.get("title")), uploader=_text(info.get("uploader")),
            duration_seconds=_number(info.get("duration")), formats=formats,
        )
    except ValidationError as exc:
        raise VideoUnavailableError("Extractor returned invalid video metadata.") from exc


async def run_worker(action: str, payload: Path, timeout: float) -> None:
    code, stdout, _ = await run_process(
        sys.executable, "-m", "bot.services.ytdlp_worker", action, str(payload), timeout=timeout,
    )
    if code:
        try:
            error = json.loads(stdout)
            message = str(error.get("error", "unavailable"))
        except (ValueError, AttributeError):
            message = "Extractor process failed"
        raise classify_error(message)


class YtdlpExtractor:
    """Use yt-dlp without cookies, authentication, or geo bypass."""

    def __init__(self, timeout: float = 60) -> None:
        self.timeout = timeout

    async def extract(self, url: str, workspace: Path) -> Extraction:
        info_path = workspace / "info.json"
        payload = workspace / "extract.json"
        payload.write_text(json.dumps({"url": url, "output": str(info_path)}), encoding="utf-8")
        await run_worker("extract", payload, self.timeout)
        def parse() -> VideoInfo:
            if not info_path.is_file() or info_path.stat().st_size > 10 * 1024 * 1024:
                raise VideoUnavailableError("Missing or oversized extractor metadata.")
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
                return metadata_from_ytdlp(info, url)
            except (ValueError, TypeError, AttributeError) as exc:
                raise VideoUnavailableError("Malformed extractor metadata.") from exc

        video = await asyncio.to_thread(parse)
        return Extraction(video, info_path)
