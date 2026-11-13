"""Strict TikTok input validation and bounded, host-checked short redirects."""

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from bot.models.exceptions import DownloadTimeoutError, InvalidURLError, NetworkError, classify_error

TIKTOK_HOSTS = frozenset({"www.tiktok.com", "tiktok.com", "vm.tiktok.com", "vt.tiktok.com"})
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com"})
HOSTS = TIKTOK_HOSTS | YOUTUBE_HOSTS | INSTAGRAM_HOSTS
VIDEO_PATH = re.compile(r"/@[A-Za-z0-9_.-]+/(?:video|photo)/[0-9]+/?\Z")
SHORT_PATH = re.compile(r"/(?:t/)?[A-Za-z0-9_-]+/?\Z")
YOUTUBE_PATH = re.compile(r"/shorts/([A-Za-z0-9_-]{11})/?\Z")
YOUTUBE_SHORT_PATH = re.compile(r"/([A-Za-z0-9_-]{11})/?\Z")
INSTAGRAM_PATH = re.compile(r"/reels?/([A-Za-z0-9_-]+)/?\Z")


def normalize_url(value: str) -> str:
    """Validate individual clips on exact platform hosts, stripping tracking."""
    value = value.strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise InvalidURLError("Invalid characters in URL.")
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
    except ValueError as exc:
        raise InvalidURLError("Malformed URL.") from exc
    if (parts.scheme not in {"https", "http"} or host not in HOSTS
            or parts.username is not None or parts.password is not None
            or port not in {None, 443}):
        raise InvalidURLError("Only supported public clip URLs on standard HTTPS are accepted.")
    if host in YOUTUBE_HOSTS:
        match = (YOUTUBE_SHORT_PATH if host == "youtu.be" else YOUTUBE_PATH).fullmatch(parts.path)
        if not match:
            raise InvalidURLError("Expected an individual YouTube Shorts link.")
        return f"https://www.youtube.com/shorts/{match[1]}"
    if host in INSTAGRAM_HOSTS:
        match = INSTAGRAM_PATH.fullmatch(parts.path)
        if not match:
            raise InvalidURLError("Expected an individual Instagram Reel link.")
        return f"https://www.instagram.com/reel/{match[1]}/"
    if host in {"www.tiktok.com", "tiktok.com"} and VIDEO_PATH.fullmatch(parts.path):
        host = "www.tiktok.com"
        path = parts.path.rstrip("/")
    elif (host in {"vm.tiktok.com", "vt.tiktok.com"}
          or parts.path.startswith("/t/")) and SHORT_PATH.fullmatch(parts.path):
        path = parts.path.rstrip("/") + "/"
    else:
        raise InvalidURLError("Expected a public video URL or a TikTok short link.")
    return urlunsplit(("https", host, path, "", ""))


async def resolve_url(value: str, client: httpx.AsyncClient) -> str:
    """Resolve short links without downloading bodies or following unchecked hosts."""
    current = normalize_url(value)
    if urlsplit(current).hostname not in TIKTOK_HOSTS:
        return current
    seen: set[str] = set()
    for _ in range(6):
        if VIDEO_PATH.fullmatch(urlsplit(current).path):
            return current
        if current in seen:
            raise InvalidURLError("TikTok redirect loop.")
        seen.add(current)
        try:
            async with client.stream("GET", current, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise InvalidURLError("Redirect without a destination.")
                    current = normalize_url(urljoin(current, location))
                    if urlsplit(current).hostname not in TIKTOK_HOSTS:
                        raise InvalidURLError("TikTok redirects cannot switch platforms.")
                elif response.is_error:
                    raise classify_error(f"HTTP Error {response.status_code}")
                else:
                    raise InvalidURLError("Short link did not resolve to a public video.")
        except httpx.TimeoutException as exc:
            raise DownloadTimeoutError("TikTok redirect timed out.") from exc
        except httpx.HTTPError as exc:
            raise NetworkError("Failed to resolve the TikTok link.") from exc
    raise InvalidURLError("Too many TikTok redirects.")
