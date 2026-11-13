"""Bounded local soundtrack conversion; publish only a verified MP3."""

import asyncio
from pathlib import Path

from bot.utils.performance import scoped, stage

from bot.models.exceptions import CorruptFileError, FileSizeLimitError, DownloadTimeoutError
from bot.services.media import require_media_tools, verify_media
from bot.services.conversion import ConversionLimiter
from bot.utils.process import run_process


@scoped
async def extract_audio(source: Path, output: Path, *, max_bytes: int, timeout: float,
                        full_decode: bool = False, conversions: ConversionLimiter | None = None):
    require_media_tools()
    try:
        async with asyncio.timeout(timeout), (conversions or ConversionLimiter()).slot():
            with stage("conversion"):
                code, _, _ = await run_process(
                    'ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-y',
                    '-protocol_whitelist', 'file,pipe', '-i', str(source),
                    '-map', '0:a:0', '-vn', '-map_metadata', '-1',
                    '-c:a', 'libmp3lame', '-b:a', '192k',
                    '-threads', '1', '-fs', str(max_bytes + 1), str(output), timeout=timeout,
                )
                if output.is_file() and output.stat().st_size > max_bytes:
                    raise FileSizeLimitError('Extracted audio exceeds the file size limit.')
                if code:
                    raise CorruptFileError('Audio extraction failed or the source has no audio.')
            with stage("verification"):
                verified = await verify_media(output, timeout=timeout, full_decode=full_decode, audio_only=True)
                return verified.model_copy(update={'transcoded': True, 'remuxed': False})
    except TimeoutError as exc:
        output.unlink(missing_ok=True)
        raise DownloadTimeoutError('Audio conversion deadline exceeded.') from exc
    except BaseException:
        output.unlink(missing_ok=True)
        raise
