"""Probe actual streams and decode-test integrity without modifying the video."""

from fractions import Fraction
import json
import math
from pathlib import Path
import shutil
import stat
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


def probed_format(data: dict[str, Any], path: Path, *, audio_only: bool = False) -> VideoFormat:
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio_only:
        if video or not audio:
            raise CorruptFileError("Expected an audio-only file with a readable audio stream.")
        if "nb_read_packets" in audio and not _positive_int(audio["nb_read_packets"]):
            raise CorruptFileError("The audio stream contains no readable packets.")
    elif not video:
        raise CorruptFileError("No video stream found in downloaded file.")
    video = video or {}
    width, height = _positive_int(video.get("width")), _positive_int(video.get("height"))
    if not audio_only and (not width or not height):
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
        fps=fps, video_codec=video.get("codec_name"), pixel_format=video.get("pix_fmt"),
        container=(data.get("format") or {}).get("format_name"),
        audio_codec=audio.get("codec_name") if audio else "none",
        has_video=not audio_only, has_audio=audio is not None,
        tbr=bitrate / 1000 if bitrate else None, filesize=path.stat().st_size,
        duration_seconds=duration,
    )


def file_identity(path: Path) -> tuple[int, ...]:
    """Do not follow a final symlink; atime changes from readers are harmless."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not info.st_size:
        raise CorruptFileError("Downloaded file is missing or empty.")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def bind_verification(verified: VideoFormat, path: Path) -> None:
    """Bind after a successful probe or our controlled atomic publication only."""
    verified._file_identity = file_identity(path)
    verified._verified_data = verified.model_dump_json(exclude={'remuxed', 'transcoded'})


def verification_matches(path: Path, verified: VideoFormat | None) -> bool:
    if verified is None or verified._file_identity is None:
        return False
    try:
        return (file_identity(path) == verified._file_identity
                and verified.model_dump_json(exclude={'remuxed', 'transcoded'}) == verified._verified_data)
    except (OSError, CorruptFileError):
        return False


async def verify_media(path: Path, *, timeout: float = 120, full_decode: bool = False,
                       audio_only: bool = False) -> VideoFormat:
    """Probe all packets; optionally decode fully when deeper integrity is required."""
    require_media_tools(require_ffmpeg=full_decode)
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise CorruptFileError("Downloaded file is missing or empty.")
    identity = file_identity(path)
    code, stdout, stderr = await run_process(
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-count_packets",
        "-show_entries", "stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,nb_read_packets:stream_disposition=attached_pic:format=bit_rate,duration,format_name",
        "-of", "json",
        str(path), timeout=timeout,
    )
    if code or stderr.strip():
        raise CorruptFileError("ffprobe rejected the downloaded container.")
    try:
        verified = probed_format(json.loads(stdout), path, audio_only=audio_only)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise CorruptFileError("Invalid ffprobe output.") from exc
    if not full_decode:
        if file_identity(path) != identity:
            raise CorruptFileError("File changed during verification.")
        bind_verification(verified, path)
        return verified
    code, _, _ = await run_process(
        "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
        "-protocol_whitelist", "file,pipe", "-threads", "1",
        "-i", str(path), "-map", "0:a:0" if audio_only else "0:v:0", "-map", "0:a?", "-f", "null", "-",
        timeout=timeout,
    )
    if code:
        raise CorruptFileError("The downloaded streams failed full decode verification.")
    if file_identity(path) != identity:
        raise CorruptFileError("File changed during verification.")
    bind_verification(verified, path)
    return verified
