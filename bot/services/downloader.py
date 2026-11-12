"""Async download orchestration with private workspaces and atomic publication."""

import asyncio
import errno
import json
import os
from pathlib import Path
import shutil
import tempfile

import httpx
from contextlib import nullcontext

from bot.config import Settings
from bot.models import DownloadResult
from bot.models.exceptions import (
    ApplicationError, CorruptFileError, DownloadTimeoutError, FileSizeLimitError,
    InsufficientSpaceError,
)
from bot.services.extraction import Extractor, YtdlpExtractor, run_worker
from bot.services.media import require_media_tools, verify_media
from bot.services.quality import select_formats
from bot.services.urls import resolve_url
from bot.utils.network import public_http_client
from bot.services.files import FileRegistry


class TikTokDownloader:
    """One instance bounds concurrency across all its download calls."""

    def __init__(
        self, settings: Settings, *, extractor: Extractor | None = None,
        timeout: float = 240, client: httpx.AsyncClient | None = None, registry: FileRegistry | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.settings = settings
        self.extractor = extractor or YtdlpExtractor(timeout=min(timeout, 60))
        self.timeout = timeout
        self.client = client
        self.registry = registry
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)

    async def download(self, url: str) -> DownloadResult:
        """Download one public video; clean partial files on every failure/cancel."""
        try:
            async with asyncio.timeout(self.timeout):
                async with self._semaphore:
                    if self.client is not None:
                        canonical = await resolve_url(url, self.client)
                    else:
                        async with public_http_client(limit=self.settings.http_max_connections) as client:
                            canonical = await resolve_url(url, client)
                    return await self._download(canonical)
        except TimeoutError as exc:
            raise DownloadTimeoutError("Total download deadline exceeded.") from exc
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise InsufficientSpaceError("Insufficient disk space.") from exc
            raise ApplicationError("Local filesystem operation failed.") from exc

    async def _download(self, url: str) -> DownloadResult:
        require_media_tools(require_ffmpeg=False)
        base = self.settings.download_dir.expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
        limit = self.settings.max_file_size_mb * 1024 * 1024
        with tempfile.TemporaryDirectory(prefix=".tiktok-", dir=base) as temporary, (
            self.registry.track(Path(temporary)) if self.registry else nullcontext()
        ):
            workspace = Path(temporary)
            extraction = await self.extractor.extract(url, workspace)
            selection = select_formats(extraction.video.formats)
            if selection.known_size_bytes > limit:
                raise FileSizeLimitError("Selected original exceeds MAX_FILE_SIZE_MB; no quality reduction attempted.")
            needed = min(selection.estimated_size or limit, limit) * 3 + 1024 * 1024
            if shutil.disk_usage(base).free < needed:
                raise InsufficientSpaceError("Not enough free space for download, verification and optional merge.")
            media_dir = workspace / "media"
            media_dir.mkdir()
            payload = workspace / "download.json"
            payload.write_text(json.dumps({
                "info_path": str(extraction.info_path), "video_id": selection.video.format_id,
                "audio_id": selection.audio.format_id if selection.audio else None,
                "media_dir": str(media_dir), "max_bytes": limit,
            }), encoding="utf-8")
            await run_worker("download", payload, self.timeout)
            files = [p for p in media_dir.iterdir() if p.name.startswith("original.")
                     and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".flv", ".ts"}
                     and p.is_file() and not p.is_symlink()]
            if len(files) != 1:
                raise CorruptFileError("Downloader did not produce exactly one complete media file.")
            source = files[0]
            size = source.stat().st_size
            if size > limit:
                raise FileSizeLimitError("Downloaded original exceeds MAX_FILE_SIZE_MB.")
            verified = await verify_media(source, timeout=self.timeout, full_decode=self.settings.verify_full_decode)
            expected_audio = selection.audio is not None or selection.video.has_audio is True
            if expected_audio and not verified.has_audio:
                raise CorruptFileError("Expected audio stream is missing from the downloaded file.")
            expected_duration = extraction.video.duration_seconds
            actual_duration = verified.duration_seconds
            if expected_duration and actual_duration:
                tolerance = max(1.0, expected_duration * 0.05)
                if abs(expected_duration - actual_duration) > tolerance:
                    raise CorruptFileError("Downloaded duration differs substantially from source metadata.")
            warnings = []
            if selection.video.has_watermark is True:
                warnings.append("Selected variant is marked as watermarked by the extractor.")
            elif selection.video.has_watermark is None:
                warnings.append("Watermark status is unknown; absence was not verified.")
            else:
                warnings.append("Extractor explicitly reports no watermark; no visual watermark detection was performed.")
            if (selection.video.width, selection.video.height) != (verified.width, verified.height):
                warnings.append("Reported dimensions differ or are missing; verified_format contains the actual dimensions.")
            if not verified.has_audio:
                warnings.append("The downloaded original contains no audio stream.")
            descriptor, name = tempfile.mkstemp(prefix="tiktok-", suffix=source.suffix, dir=base)
            os.close(descriptor)
            destination = Path(name)
            try:
                result = DownloadResult(
                    video_info=extraction.video, format=selection.video,
                    file_path=destination, file_size_bytes=size,
                    verified_format=verified, warnings=warnings,
                )
                os.replace(source, destination)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
            return result
