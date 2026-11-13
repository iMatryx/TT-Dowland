"""Prepare verified Telegram video: reuse, lossless remux, then encode if needed."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from bot.models import VideoFormat
from bot.models.exceptions import CorruptFileError, DownloadTimeoutError, FileSizeLimitError
from bot.services.conversion import ConversionLimiter
from bot.services.media import require_media_tools, verify_media, verification_matches
from bot.utils.performance import scoped, stage
from bot.utils.process import run_process


@dataclass(frozen=True)
class PreparedVideo:
    path: Path
    format: VideoFormat


def copy_compatible(verified: VideoFormat) -> bool:
    return (verified.video_codec in {'h264', 'avc1'}
            and verified.pixel_format in {'yuv420p', 'yuvj420p'}
            and (verified.has_audio is False or verified.audio_codec in {'aac', 'mp4a'}))


def mp4_compatible(path: Path, verified: VideoFormat) -> bool:
    return (path.suffix.lower() == '.mp4' and copy_compatible(verified)
            and 'mp4' in (verified.container or '').split(','))


def _check_result(source: VideoFormat, result: VideoFormat) -> None:
    if source.has_audio and not result.has_audio:
        raise CorruptFileError('Prepared video lost the original audio.')
    if source.duration_seconds and result.duration_seconds:
        if abs(source.duration_seconds - result.duration_seconds) > max(.25, source.duration_seconds * .05):
            raise CorruptFileError('Prepared video duration differs from the original.')


async def _attempt(source: Path, output: Path, original: VideoFormat, *, copy: bool,
                   max_bytes: int, timeout: float) -> PreparedVideo:
    output.unlink(missing_ok=True)
    codecs = ['-c', 'copy'] if copy else [
        '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '192k', '-threads', '1',
    ]
    with stage('conversion'):
        code, _, _ = await run_process(
            'ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-y',
            '-protocol_whitelist', 'file,pipe', '-i', str(source),
            '-map', '0:V:0', '-map', '0:a:0?', '-map_metadata', '-1',
            *codecs, '-movflags', '+faststart', '-fs', str(max_bytes + 1),
            '-f', 'mp4', str(output), timeout=timeout,
        )
        if output.is_file() and output.stat().st_size > max_bytes:
            raise FileSizeLimitError('Prepared video exceeds the Telegram upload limit.')
        if code:
            raise CorruptFileError('Could not prepare a playable video.')
    with stage('verification'):
        verified = await verify_media(output, timeout=timeout)
        if not mp4_compatible(output, verified):
            raise CorruptFileError('Prepared video is incompatible with Telegram.')
        _check_result(original, verified)
    return PreparedVideo(output, verified.model_copy(update={'remuxed': copy, 'transcoded': not copy}))


@scoped
async def create_preview(source: Path, output: Path, *, max_bytes: int, timeout: float,
                         conversions: ConversionLimiter | None = None,
                         verified: VideoFormat | None = None) -> PreparedVideo:
    # Check before cleanup: the original must never become an output or be unlinked.
    if source.resolve() == output.resolve() or output.is_symlink():
        raise CorruptFileError('Preparation requires a separate output file.')
    if timeout <= 0 or max_bytes <= 0:
        raise ValueError('Preparation limits must be positive')
    try:
        async with asyncio.timeout(timeout):
            with stage('verification'):
                original = verified if verification_matches(source, verified) else await verify_media(source, timeout=timeout)
            if source.stat().st_size <= max_bytes and mp4_compatible(source, original):
                return PreparedVideo(source, original)
            require_media_tools()
            async with (conversions or ConversionLimiter()).slot():
                if copy_compatible(original):
                    try:
                        return await _attempt(source, output, original, copy=True, max_bytes=max_bytes,
                                              timeout=min(30, timeout / 3))
                    except (CorruptFileError, FileSizeLimitError, DownloadTimeoutError):
                        output.unlink(missing_ok=True)
                return await _attempt(source, output, original, copy=False, max_bytes=max_bytes, timeout=timeout)
    except TimeoutError as exc:
        output.unlink(missing_ok=True)
        raise DownloadTimeoutError('Video preparation deadline exceeded.') from exc
    except BaseException:
        output.unlink(missing_ok=True)
        raise
