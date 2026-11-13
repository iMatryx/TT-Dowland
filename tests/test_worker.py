import json
from pathlib import Path

import pytest

from bot.services import ytdlp_worker


def test_separate_stream_selection_keeps_originals() -> None:
    video = {"format_id": "video", "protocol": "https", "ext": "mp4", "vcodec": "h264"}
    audio = {"format_id": "audio", "protocol": "https", "ext": "m4a", "acodec": "aac"}
    selected = list(ytdlp_worker.selection_callback("video", "audio")({"formats": [video, audio]}))
    assert selected[0]["requested_formats"] == [video, audio]
    assert selected[0]["ext"] == "mkv"


def test_muxed_source_is_used_directly() -> None:
    source = {"format_id": "source", "protocol": "https", "ext": "mp4"}
    assert list(ytdlp_worker.selection_callback("source", None)({"formats": [source]})) == [source]


def test_download_progress_enforces_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    info_path = tmp_path / "info.json"
    info_path.write_text(json.dumps({"formats": [{
        "format_id": "source", "url": "https://cdn.example/video.mp4", "protocol": "https",
    }]}))

    sanitize = ytdlp_worker.YoutubeDL.sanitize_info

    class FakeYDL:
        sanitize_info = staticmethod(sanitize)

        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def process_ie_result(self, info, download):
            self.options["progress_hooks"][0]({"filename": "original.mp4", "downloaded_bytes": 101})

    monkeypatch.setattr(ytdlp_worker, "YoutubeDL", FakeYDL)
    with pytest.raises(ValueError, match="file size limit"):
        ytdlp_worker.download_job({
            "info_path": str(info_path), "max_bytes": 100,
            "video_id": "source", "media_dir": str(tmp_path),
        })


def test_download_rejects_file_protocol_before_yt_dlp(tmp_path: Path) -> None:
    info_path = tmp_path / "info.json"
    info_path.write_text(json.dumps({"formats": [{
        "format_id": "source", "url": "file:///etc/passwd", "protocol": "file",
    }]}))
    with pytest.raises(ValueError, match="source URL"):
        ytdlp_worker.download_job({
            "info_path": str(info_path), "max_bytes": 100,
            "video_id": "source", "media_dir": str(tmp_path),
        })


def test_real_ytdlp_reprocessing_does_not_restore_stale_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise real yt-dlp selection while replacing only actual media I/O."""
    source = {"format_id": "chosen", "ext": "mp4", "url": "https://cdn.example/chosen.mp4",
              "protocol": "https", "vcodec": "h264", "acodec": "aac", "width": 720, "height": 1280}
    stale_video = {"format_id": "old-video", "ext": "mp4", "url": "https://cdn.example/old.mp4",
                   "protocol": "https", "vcodec": "h264", "acodec": "none"}
    stale_audio = {"format_id": "old-audio", "ext": "m4a", "url": "https://cdn.example/old.m4a",
                   "protocol": "https", "vcodec": "none", "acodec": "aac"}
    info = {
        "id": "123", "title": "Fixture", "extractor": "TikTok", "extractor_key": "TikTok",
        "formats": [source, stale_video, stale_audio],
        "requested_formats": [stale_video, stale_audio], "ext": "mkv",
        "format_id": "old-video+old-audio", "protocol": "https+https",
    }
    info_path = tmp_path / "info.json"
    info_path.write_text(json.dumps(info))
    captured = []

    class InspectYDL(ytdlp_worker.YoutubeDL):
        def process_info(self, info_dict):
            captured.append(info_dict.copy())

    monkeypatch.setattr(ytdlp_worker, "YoutubeDL", InspectYDL)
    ytdlp_worker.download_job({
        "info_path": str(info_path), "max_bytes": 1000,
        "video_id": "chosen", "media_dir": str(tmp_path),
    })
    assert len(captured) == 1
    assert captured[0]["format_id"] == "chosen"
    assert captured[0]["url"] == "https://cdn.example/chosen.mp4"
    assert not captured[0].get("requested_formats")


def test_merger_disables_remote_protocols_on_every_input() -> None:
    from yt_dlp.postprocessor.ffmpeg import FFmpegMergerPP

    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        merger = FFmpegMergerPP(ydl)
        assert merger._configuration_args("ffmpeg", ["_i1", "_i"]) == ["-protocol_whitelist", "file,pipe"]
        assert merger._configuration_args("ffmpeg", ["_i2", "_i"]) == ["-protocol_whitelist", "file,pipe"]


def test_metadata_selection_does_not_probe_ffmpeg(monkeypatch) -> None:
    from yt_dlp.postprocessor.ffmpeg import FFmpegMergerPP

    def unnecessary_probe(self):
        raise AssertionError("Metadata extraction must not invoke FFmpeg capability checks")

    monkeypatch.setattr(FFmpegMergerPP, "available", property(unnecessary_probe))
    info = {"id": "123", "title": "Fixture", "extractor": "TikTok", "formats": [{
        "format_id": "source", "url": "https://cdn.example/video.mp4", "ext": "mp4",
        "protocol": "https", "vcodec": "h264", "acodec": "aac",
    }]}
    with ytdlp_worker.YoutubeDL(ytdlp_worker.base_options()) as ydl:
        assert ydl.process_ie_result(info, download=False)["id"] == "123"


def test_postprocessing_records_only_timing_data(tmp_path):
    from yt_dlp.postprocessor.common import PostProcessor
    class LocalPP(PostProcessor):
        def run(self, info):
            return [], info
    timings = tmp_path / 'timings.json'
    with ytdlp_worker.YoutubeDL({**ytdlp_worker.base_options(), '_performance_path': str(timings)}) as ydl:
        ydl.run_pp(LocalPP(ydl), {'id': 'personal', 'url': 'https://signed.example/?secret=private'})
    data = json.loads(timings.read_text())
    assert len(data) == 1
    assert data[0]['result'] == 'success'
    assert data[0]['finished'] >= data[0]['started']
    assert 'personal' not in timings.read_text() and 'private' not in timings.read_text()
