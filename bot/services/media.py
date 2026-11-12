"""Probe actual streams and decode-test integrity without modifying the video."""

from fractions import Fraction
import json
import math
from pathlib import Path
import shutil
from typing import Any

from bot.models import VideoFormat
from bot.models.exceptions import CorruptFileError, MissingFFmpegError
from bot.utils.process import run_process


def require_media_tools(*, require_ffmpeg: bool = True) -> None:
    tools = ("ffprobe", "ffmpeg") if require_ffmpeg else ("ffprobe",)
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise MissingFFmpegError(f"Required executables missing from PATH: {', '.join(missing)}")


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def probed_format(data: dict[str, Any], path: Path) -> VideoFormat:
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise CorruptFileError("No video stream found in downloaded file.")
    width, height = _positive_int(video.get("width")), _positive_int(video.get("height"))
    if not width or not height:
        raise CorruptFileError("ffprobe could not confirm the video dimensions.")
    if "nb_read_packets" in video and not _positive_int(video["nb_read_packets"]):
        raise CorruptFileError("The video stream contains no readable packets.")
    fps = None
    try:
        fraction = Fraction(video.get("avg_frame_rate", "0/0"))
        fps = float(fraction) if fraction > 0 else None
    except (ValueError, ZeroDivisionError, TypeError):
        pass
    bitrate = _positive_int((data.get("format") or {}).get("bit_rate"))
    duration = None
    try:
        reported_duration = float((data.get("format") or {}).get("duration"))
        if math.isfinite(reported_duration) and reported_duration > 0:
            duration = reported_duration
    except (TypeError, ValueError):
        pass
    return VideoFormat(
        format_id="ffprobe", ext=path.suffix.lstrip("."), width=width, height=height,
        fps=fps, video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name") if audio else "none",
        has_video=True, has_audio=audio is not None,
        tbr=bitrate / 1000 if bitrate else None, filesize=path.stat().st_size,
        duration_seconds=duration,
    )


async def verify_media(path: Path, *, timeout: float = 120, full_decode: bool = False) -> VideoFormat:
    """Probe all packets; optionally decode fully when deeper integrity is required."""
    require_media_tools(require_ffmpeg=full_decode)
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise CorruptFileError("Downloaded file is missing or empty.")
    code, stdout, stderr = await run_process(
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-count_packets",
        "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_read_packets:stream_disposition=attached_pic:format=bit_rate,duration",
        "-of", "json",
        str(path), timeout=timeout,
    )
    if code or stderr.strip():
        raise CorruptFileError("ffprobe rejected the downloaded container.")
    try:
        verified = probed_format(json.loads(stdout), path)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise CorruptFileError("Invalid ffprobe output.") from exc
    if not full_decode:
        return verified
    code, _, _ = await run_process(
        "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
        "-protocol_whitelist", "file,pipe", "-threads", "1",
        "-i", str(path), "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-",
        timeout=timeout,
    )
    if code:
        raise CorruptFileError("The downloaded streams failed full decode verification.")
    return verified
