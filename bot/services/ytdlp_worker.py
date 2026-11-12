"""Internal subprocess adapter; receives only locally generated job files."""

import json
from functools import cached_property
from pathlib import Path
import sys
from typing import Any, Iterator
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL as BaseYoutubeDL
from yt_dlp.networking._urllib import UrllibRH
from yt_dlp.globals import plugin_dirs

from bot.services.urls import normalize_url
from bot.utils.network import guard_sockets


class YoutubeDL(BaseYoutubeDL):
    """Use only the stdlib network handler covered by the socket guard."""

    @cached_property
    def _request_director(self):
        return self.build_request_director([UrllibRH])


class QuietLogger:
    """Avoid leaking signed CDN URLs or huge diagnostic output to the CLI."""

    def debug(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None


def base_options() -> dict[str, Any]:
    return {
        "quiet": True, "no_warnings": True, "noprogress": True,
        # Metadata selection must not run FFmpeg availability probes.
        # The download job replaces this with our original-quality selector.
        "format": "best/bestvideo+bestaudio",
        "logger": QuietLogger(), "noplaylist": True, "geo_bypass": False,
        "socket_timeout": 20, "retries": 0, "fragment_retries": 0,
        "extractor_retries": 0, "skip_unavailable_fragments": False,
        "proxy": "", "postprocessor_args": {"merger+ffmpeg_i": ["-protocol_whitelist", "file,pipe"]},
        "concurrent_fragment_downloads": 1, "cachedir": False,
        "cookiefile": None, "cookiesfrombrowser": None,
        "enable_file_urls": False, "fixup": "never",
        "overwrites": True, "continuedl": False,
    }


def selection_callback(video_id: str, audio_id: str | None) -> Any:
    def select(context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        formats = context["formats"]
        video = next(f for f in formats if f["format_id"] == video_id)
        if audio_id is None:
            yield video
            return
        audio = next(f for f in formats if f["format_id"] == audio_id)
        yield {
            "format_id": f'{video["format_id"]}+{audio["format_id"]}',
            "ext": "mkv", "requested_formats": [video, audio],
            "protocol": f'{video["protocol"]}+{audio["protocol"]}',
        }
    return select


def download_job(payload: dict[str, Any]) -> None:
    info = YoutubeDL.sanitize_info(
        json.loads(Path(payload["info_path"]).read_text(encoding="utf-8")),
        remove_private_keys=True,
    )
    limit = int(payload["max_bytes"])
    byte_counts: dict[str, int] = {}

    def progress(status: dict[str, Any]) -> None:
        key = str(status.get("filename", "stream"))
        byte_counts[key] = max(byte_counts.get(key, 0), status.get("downloaded_bytes") or 0)
        if sum(byte_counts.values()) > limit:
            raise ValueError("file size limit exceeded")
        total = status.get("total_bytes")
        if total and total + sum(count for name, count in byte_counts.items() if name != key) > limit:
            raise ValueError("file size limit exceeded")

    # File URLs and unexpected protocols must never reach a downloader.
    for fmt in info.get("formats") or []:
        if fmt.get("format_id") not in {payload["video_id"], payload.get("audio_id")}:
            continue
        parts = urlsplit(fmt.get("url", ""))
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
            raise ValueError("Unsupported source URL")
        if fmt.get("protocol", "https") not in {"http", "https"}:
            raise ValueError("Unsupported source protocol")
    options = {
        **base_options(),
        "format": selection_callback(payload["video_id"], payload.get("audio_id")),
        "outtmpl": str(Path(payload["media_dir"]) / "original.%(ext)s"),
        "merge_output_format": "mkv", "progress_hooks": [progress],
    }
    with YoutubeDL(options) as ydl:
        ydl.process_ie_result(info, download=True)


def worker_main() -> int:
    try:
        action, payload_path = sys.argv[1:]
        payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
        if action == "extract":
            url = normalize_url(payload["url"])
            with YoutubeDL(base_options()) as ydl:
                info = ydl.extract_info(url, download=False, ie_key="TikTok")
                if not isinstance(info, dict) or info.get("_type", "video") != "video":
                    raise ValueError("Unavailable individual video")
                Path(payload["output"]).write_text(
                    json.dumps(ydl.sanitize_info(info, remove_private_keys=True)), encoding="utf-8",
                )
        elif action == "download":
            download_job(payload)
        else:
            raise ValueError("Invalid worker action")
    except Exception as exc:
        # The parent maps this internally and only prints a sanitized domain error.
        print(json.dumps({"error": str(exc)[-4000:]}))
        return 1
    return 0


def main() -> int:
    plugin_dirs.value = []
    with guard_sockets():
        return worker_main()


if __name__ == "__main__":
    raise SystemExit(main())
