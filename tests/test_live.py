"""Opt in explicitly: TIKTOK_TEST_URL=https://... pytest -m live -s."""

import os

import pytest

from bot.config import Settings
from bot.services.downloader import TikTokDownloader


@pytest.mark.live
@pytest.mark.asyncio
async def test_public_video_live(tmp_path) -> None:
    url = os.environ.get("TIKTOK_TEST_URL")
    if not url:
        pytest.skip("Set TIKTOK_TEST_URL to explicitly enable a real public download")
    result = await TikTokDownloader(Settings(DOWNLOAD_DIR=tmp_path)).download(url)
    assert result.file_path.is_file()
    assert result.file_size_bytes > 0
    assert result.verified_format.width > 0
    assert result.verified_format.height > 0
    print(result.model_dump_json(indent=2))
