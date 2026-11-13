"""Async download orchestration with private workspaces and atomic publication."""

import asyncio
from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import shutil
import tempfile

import httpx
from contextlib import nullcontext

from bot.utils.performance import scoped, stage, queued_slot, download_stage
from bot.config import Settings
from bot.models import DownloadResult
from bot.models.exceptions import (
    ApplicationError, CorruptFileError, DownloadTimeoutError, FileSizeLimitError,
    InsufficientSpaceError,
)
from bot.services.extraction import Extractor, YtdlpExtractor, run_worker, CachedExtractionError, media_transfer
from bot.services.analysis_cache import CachedExtractor
from bot.services.media import require_media_tools, verify_media, bind_verification
from bot.services.quality import FormatSelection, select_audio, select_formats, select_photos
from bot.services.urls import resolve_url
from bot.utils.network import public_http_client
from bot.services.files import FileRegistry
from bot.services.conversion import ConversionLimiter


class TikTokDownloader:
    """One instance bounds concurrency across all its download calls."""

    def __init__(
        self, settings: Settings, *, extractor: Extractor | None = None,
        conversions: ConversionLimiter | None = None,
        timeout: float = 240, client: httpx.AsyncClient | None = None, registry: FileRegistry | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.settings = settings
        self.extractor = CachedExtractor(
            extractor or YtdlpExtractor(timeout=min(timeout, 60)), root=settings.download_dir.expanduser().resolve(),
            ttl=settings.analysis_cache_ttl_seconds, capacity=settings.analysis_cache_capacity,
            enabled=settings.cache_enabled, concurrency=settings.max_concurrent_downloads,
            max_pending=settings.max_pending_requests, registry=registry)
        self.timeout = timeout
        self.client = client
        self.registry = registry
        self.conversions = conversions or ConversionLimiter(settings.max_concurrent_conversions)
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)

    @scoped
    async def download(self, url: str, *, preference: str = "clean", quality: str = "high", watermark: str | None = None, audio_only: bool = False) -> DownloadResult:
        """Download one public video; clean partial files on every failure/cancel."""
        quality, watermark = "high", "without"
        try:
            async with asyncio.timeout(self.timeout):
                async with queued_slot(self._semaphore, queue="download"):
                    with stage("analysis"):
                        if self.client is not None:
                            canonical = await resolve_url(url, self.client)
                        else:
                            async with public_http_client(limit=self.settings.http_max_connections) as client:
                                canonical = await resolve_url(url, client)
                    for attempt in range(2):
                        try:
                            result = await self._download(canonical, preference=preference, quality=quality,
                                                          watermark=watermark, audio_only=audio_only)
                            break
                        except CachedExtractionError as exc:
                            self.extractor.invalidate(canonical, exc.token)
                            if attempt:
                                raise exc.error from exc
                # No download permit is held while waiting for or encoding audio.
                if audio_only:
                    return await self._convert_audio(result, quality=quality)
                return result
        except TimeoutError as exc:
            raise DownloadTimeoutError("Total download deadline exceeded.") from exc
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise InsufficientSpaceError("Insufficient disk space.") from exc
            raise ApplicationError("Local filesystem operation failed.") from exc

    async def _download(self, url: str, *, preference: str = "clean", quality: str = "high", watermark: str | None = None, audio_only: bool = False) -> DownloadResult:
        quality, watermark = "high", "without"
        require_media_tools(require_ffmpeg=False)
        base = self.settings.download_dir.expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
        limit = self.settings.max_file_size_mb * 1024 * 1024
        with tempfile.TemporaryDirectory(prefix=".tiktok-", dir=base) as temporary, (
            self.registry.track(Path(temporary)) if self.registry else nullcontext()
        ):
            workspace = Path(temporary)
            with stage("analysis"):
                extraction = await self.extractor.extract(url, workspace)
            if extraction.video.media_type == "photo":
                if audio_only:
                    raise CorruptFileError("Audio extraction requires a video post.")
                from bot.services.photos import download_photos
                selected = select_photos(extraction.video, quality=quality,
                                         watermark="without")
                extraction = replace(extraction, video=selected)
                return await download_photos(extraction, workspace, self.settings, self.timeout)
            selection = (FormatSelection(select_audio(extraction.video.formats, quality=quality)) if audio_only
                         else select_formats(extraction.video.formats, preference=preference, quality=quality, watermark=watermark))
            if selection.known_size_bytes > limit:
                raise FileSizeLimitError("Selected original exceeds MAX_FILE_SIZE_MB; no quality reduction attempted.")
            needed = min(selection.estimated_size or limit, limit) * 3 + 1024 * 1024
            if shutil.disk_usage(base).free < needed:
                raise InsufficientSpaceError("Not enough free space for download, verification and optional merge.")
            media_dir = workspace / "media"
            media_dir.mkdir()
            payload = workspace / "download.json"
            timings = workspace / "worker-timings.json"
            payload.write_text(json.dumps({
                "info_path": str(extraction.info_path), "video_id": selection.video.format_id,
                "audio_id": selection.audio.format_id if selection.audio else None,
                "media_dir": str(media_dir), "max_bytes": limit, "performance_path": str(timings),
            }), encoding="utf-8")
            with media_transfer(extraction), download_stage(timings):
                await run_worker("download", payload, self.timeout)
            with stage("verification"):
                files = [p for p in media_dir.iterdir() if p.name.startswith("original.")
                         and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".flv", ".ts", ".m4a", ".mp3", ".ogg", ".opus", ".aac"}
                         and p.is_file() and not p.is_symlink()]
                if len(files) != 1:
                    raise CorruptFileError("Downloader did not produce exactly one complete media file.")
                source = files[0]
                size = source.stat().st_size
                if size > limit:
                    raise FileSizeLimitError("Downloaded original exceeds MAX_FILE_SIZE_MB.")
            with stage("verification"):
                audio_probe = audio_only and (selection.video.has_video is False or selection.video.video_codec == 'none')
                verified = await verify_media(source, timeout=self.timeout, full_decode=self.settings.verify_full_decode,
                                              **({'audio_only': True} if audio_probe else {}))
            with stage("verification"):
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
                    verified_format=verified, warnings=warnings, audio_only=audio_only,
                )
                os.replace(source, destination)
                bind_verification(verified, destination)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
            return result


    async def _convert_audio(self, result: DownloadResult, *, quality: str) -> DownloadResult:
        from bot.services.audio import extract_audio
        output = None
        try:
            descriptor, name = tempfile.mkstemp(prefix='tiktok-', suffix='.mp3', dir=result.file_path.parent)
            os.close(descriptor)
            output = Path(name)
            with (self.registry.track(result.file_path) if self.registry else nullcontext()), (
                self.registry.track(output) if self.registry else nullcontext()
            ):
                verified = await extract_audio(result.file_path, output,
                    max_bytes=self.settings.max_file_size_mb * 1024 * 1024, timeout=self.timeout,
                    quality=quality, full_decode=self.settings.verify_full_decode, conversions=self.conversions)
                expected = result.verified_format.duration_seconds if result.verified_format else None
                if expected and verified.duration_seconds:
                    if abs(expected - verified.duration_seconds) > max(1.0, expected * .05):
                        raise CorruptFileError('Extracted audio duration differs from the original.')
                return result.model_copy(update={'file_path': output, 'file_size_bytes': output.stat().st_size,
                                                 'verified_format': verified, 'audio_only': True})
        except BaseException:
            if output is not None:
                output.unlink(missing_ok=True)
            raise
        finally:
            result.cleanup()


    async def close(self) -> None:
        if isinstance(self.extractor, CachedExtractor):
            await self.extractor.close()
