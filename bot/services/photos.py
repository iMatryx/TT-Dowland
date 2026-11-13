"""Verify and publish ordered source images and a lossless ZIP container."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from bot.utils.performance import stage
from bot.config import Settings
from bot.models import DownloadResult, PhotoFile
from bot.models.exceptions import CorruptFileError, FileSizeLimitError, InsufficientSpaceError
from bot.services.extraction import Extraction, run_worker, media_transfer
from bot.services.media import verify_media


async def download_photos(extraction: Extraction, workspace: Path, settings: Settings, timeout: float) -> DownloadResult:
    limit = settings.max_file_size_mb * 1024 * 1024
    base = settings.download_dir.expanduser().resolve()
    if not extraction.video.photos:
        raise CorruptFileError('Empty source album.')
    if shutil.disk_usage(base).free < limit * 2 + 1024 * 1024:
        raise InsufficientSpaceError('Insufficient space for original photos and ZIP.')
    media = workspace / 'photos'
    media.mkdir()
    payload = workspace / 'photos.json'
    payload.write_text(json.dumps({'photos': [p.model_dump(mode='json') for p in extraction.video.photos],
                                  'url': str(extraction.video.webpage_url), 'media_dir': str(media),
                                  'max_bytes': limit}), encoding='utf-8')
    with media_transfer(extraction), stage('download'):
        await run_worker('photos', payload, timeout)
    verified = []
    extensions = {'mjpeg': 'jpg', 'png': 'png', 'webp': 'webp', 'av1': 'avif', 'hevc': 'heic'}
    total = 0
    for index in range(len(extraction.video.photos)):
        source = media / f'{index + 1:04d}.img'
        with stage("verification"):
            fmt = await verify_media(source, timeout=timeout, full_decode=True)
        if fmt.video_codec not in extensions:
            raise CorruptFileError('Unsupported original image encoding.')
        target = source.with_suffix('.' + extensions[fmt.video_codec])
        source.rename(target)
        total += target.stat().st_size
        if total > limit:
            raise FileSizeLimitError('Original album exceeds local size limit.')
        verified.append(PhotoFile(file_path=target, width=fmt.width, height=fmt.height, size=target.stat().st_size))
    archive_path = media / 'originals.zip'

    def archive() -> None:
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_STORED) as output:
            for photo in verified:
                output.write(photo.file_path, arcname=photo.file_path.name)

    # Wait for the filesystem operation even on cancellation before workspace cleanup.
    with stage("conversion"):
        task = asyncio.create_task(asyncio.to_thread(archive))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
    destination = Path(tempfile.mkdtemp(prefix='tiktok-', dir=base))
    try:
        # Replace the reserved empty directory in one rename on the same volume.
        os.replace(media, destination)
        return DownloadResult(video_info=extraction.video, file_path=destination / 'originals.zip',
                              file_size_bytes=(destination / 'originals.zip').stat().st_size,
                              photos=[p.model_copy(update={'file_path': destination / p.file_path.name}) for p in verified])
    except BaseException:
        shutil.rmtree(destination)
        raise
