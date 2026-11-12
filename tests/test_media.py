import json
from pathlib import Path

import pytest

from bot.models.exceptions import CorruptFileError, MissingFFmpegError
from bot.services import media


def probe_data() -> dict:
    return {"streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1080,
         "height": 1920, "avg_frame_rate": "30000/1001"},
        {"codec_type": "audio", "codec_name": "aac"},
    ], "format": {"bit_rate": "4500000"}}


def test_reports_actual_probed_streams(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fixture")
    result = media.probed_format(probe_data(), path)
    assert (result.width, result.height) == (1080, 1920)
    assert result.fps == pytest.approx(29.97002997)
    assert result.tbr == 4500
    assert result.video_codec == "h264"
    assert result.has_audio is True
    assert result.has_watermark is None


def test_no_invented_fps(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fixture")
    data = probe_data()
    data["streams"][0]["avg_frame_rate"] = "0/0"
    assert media.probed_format(data, path).fps is None


def test_missing_tools_are_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    with pytest.raises(MissingFFmpegError, match="ffprobe, ffmpeg"):
        media.require_media_tools()


@pytest.mark.asyncio
async def test_decode_failure_is_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(media, "require_media_tools", lambda **kwargs: None)

    async def process(*args: str, timeout: float):
        if args[0] == "ffprobe":
            return 0, json.dumps(probe_data()).encode(), b""
        return 1, b"", b"decode error"

    monkeypatch.setattr(media, "run_process", process)
    with pytest.raises(CorruptFileError, match="decode"):
        await media.verify_media(path, full_decode=True)


@pytest.mark.asyncio
async def test_empty_file_is_rejected_before_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "empty.mp4"
    path.touch()
    monkeypatch.setattr(media, "require_media_tools", lambda **kwargs: None)
    with pytest.raises(CorruptFileError, match="empty"):
        await media.verify_media(path)


@pytest.mark.asyncio
async def test_default_integrity_scan_does_not_launch_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(media, "require_media_tools", lambda **kwargs: None)
    calls = []

    async def process(*args, timeout):
        calls.append(args)
        return 0, json.dumps(probe_data()).encode(), b""

    monkeypatch.setattr(media, "run_process", process)
    result = await media.verify_media(path)
    assert result.width == 1080
    assert [call[0] for call in calls] == ["ffprobe"]
    assert "-count_packets" in calls[0]


def test_zero_packet_video_is_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.touch()
    data = probe_data()
    data["streams"][0]["nb_read_packets"] = "0"
    with pytest.raises(CorruptFileError, match="no readable packets"):
        media.probed_format(data, path)
