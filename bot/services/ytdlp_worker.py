"""Internal subprocess adapter; receives only locally generated job files."""

import json
from functools import cached_property
from pathlib import Path
import sys
import time
from typing import Any, Iterator
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL as BaseYoutubeDL
from yt_dlp.networking._urllib import UrllibRH
from yt_dlp.globals import plugin_dirs
from yt_dlp.extractor.tiktok import TikTokIE
from yt_dlp.networking import Request

from bot.services.tiktok_sources import photo_metadata, annotate_video_sources

from bot.services.urls import normalize_url
from bot.utils.network import guard_sockets


class YoutubeDL(BaseYoutubeDL):
    """Use only the stdlib network handler covered by the socket guard."""

    def run_pp(self, pp, infodict):
        path = self.params.get('_performance_path')
        if not path:
            return super().run_pp(pp, infodict)
        timings = getattr(self, '_performance_timings', [])
        self._performance_timings = timings
        record = {'started': time.monotonic()}
        timings.append(record)

        def persist():
            try:
                target = Path(path)
                temporary = target.with_suffix('.tmp')
                temporary.write_text(json.dumps(timings), encoding='utf-8')
                temporary.replace(target)
            except OSError:
                pass

        persist()  # Survives a process-group kill during FFmpeg postprocessing.
        try:
            value = super().run_pp(pp, infodict)
        except BaseException:
            record['result'] = 'error'
            raise
        else:
            record['result'] = 'success'
            return value
        finally:
            record['finished'] = time.monotonic()
            persist()

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
    bundled_deno = Path(sys.executable).with_name('deno')
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
        "js_runtimes": {"deno": {"path": str(bundled_deno)} if bundled_deno.is_file() else {}, "node": {}},
        "remote_components": set(),
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
        "_performance_path": payload.get("performance_path"),
    }
    with YoutubeDL(options) as ydl:
        ydl.process_ie_result(info, download=True)


def extract_job(url: str, ydl: YoutubeDL) -> dict:
    """Reuse upstream webpage handling and its status codes for both post types."""
    url = normalize_url(url)
    host = urlsplit(url).hostname
    if host == 'www.youtube.com':
        return ydl.extract_info(url, download=False, ie_key='Youtube')
    if host == 'www.instagram.com':
        return ydl.extract_info(url, download=False, ie_key='Instagram')
    extractor = TikTokIE(ydl)
    video_id = url.rstrip('/').rsplit('/', 1)[-1]
    # The /photo/ viewer can omit webapp.video-detail. TikTok serves the
    # same post's metadata at /video/ even when its content is imagePost.
    # Only normalize the request route; determine the kind from the item.
    page_url = url.replace('/photo/', '/video/', 1)
    item, status = extractor._extract_web_data_and_status(page_url, video_id)
    if status in (10216, 10222):
        raise ValueError('private or login required')
    if status == 10204:
        raise ValueError('IP blocked from accessing this post')
    if status or not item:
        raise ValueError(f'Post unavailable, status {status}')
    photos = photo_metadata(item, url)
    if photos is not None:
        return photos
    if '/photo/' in url:
        raise ValueError('Source did not expose photo metadata')
    info = extractor._parse_aweme_video_web(item, url, video_id)
    info.update(extractor='TikTok', extractor_key='TikTok', webpage_url=url, original_url=url)
    annotate_video_sources(info, item)
    return ydl.process_ie_result(info, download=False)


def photo_download_job(payload: dict) -> None:
    """Stream original image bytes with a total album cap and safe sockets."""
    total = 0
    limit = int(payload['max_bytes'])
    with YoutubeDL(base_options()) as ydl:
        for index, photo in enumerate(payload['photos']):
            path = Path(payload['media_dir']) / f'{index + 1:04d}.img'
            for url in photo['urls']:
                parts = urlsplit(url)
                if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username:
                    raise ValueError('Unsupported source URL')
                written = 0
                try:
                    with ydl.urlopen(Request(url, headers={'Referer': payload['url']})) as response, path.open('wb') as target:
                        while block := response.read(64 * 1024):
                            written += len(block)
                            if total + written > limit:
                                raise ValueError('file size limit exceeded')
                            target.write(block)
                    if not written:
                        raise ValueError('Empty source image')
                except Exception as exc:
                    path.unlink(missing_ok=True)
                    if isinstance(exc, ValueError) or url == photo['urls'][-1]:
                        raise
                    continue
                total += written
                break


def worker_main() -> int:
    try:
        action, payload_path = sys.argv[1:]
        payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
        if action == "extract":
            url = normalize_url(payload["url"])
            with YoutubeDL(base_options()) as ydl:
                info = extract_job(url, ydl)
                if not isinstance(info, dict) or info.get("_type", "video") != "video":
                    raise ValueError("Unavailable individual video")
                Path(payload["output"]).write_text(
                    json.dumps(ydl.sanitize_info(info, remove_private_keys=True)), encoding="utf-8",
                )
        elif action == "download":
            download_job(payload)
        elif action == "photos":
            photo_download_job(payload)
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
