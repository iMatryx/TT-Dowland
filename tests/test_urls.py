import httpx
import pytest

from bot.services.urls import normalize_url, resolve_url
from bot.models.exceptions import InvalidURLError, NetworkError


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/@user/video/123?is_from_webapp=1#part",
    "http://tiktok.com/@user/video/123",
    "tiktok.com/@user/video/123",
])
def test_normalizes_video_urls(url: str) -> None:
    assert normalize_url(url) == "https://www.tiktok.com/@user/video/123"


@pytest.mark.parametrize("url", [
    "https://tiktok.com.evil.test/@user/video/123",
    "https://evil.test/?url=https://www.tiktok.com/@user/video/123",
    "https://name:pass@www.tiktok.com/@user/video/123",
    "https://www.tiktok.com:8000/@user/video/123",
    "file:///etc/passwd", "https://www.tiktok.com/@user/live",
    "https://www.tiktok.com/@user", "https://127.0.0.1/",
    "https://vm.tiktok.com/../secret", "https://vm.tiktok.com/%2fsecret",
])
def test_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(InvalidURLError):
        normalize_url(url)


@pytest.mark.asyncio
async def test_resolves_short_link_without_reading_body() -> None:
    requests = []

    class UnreadBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("Redirect body must not be downloaded")
            yield b""  # Make this an async iterator; unreachable after the assertion.

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={
            "location": "https://www.tiktok.com/@author/video/42?tracking=1",
        }, stream=UnreadBody())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await resolve_url("https://vm.tiktok.com/ABC/", client) == (
            "https://www.tiktok.com/@author/video/42"
        )
    assert requests == ["https://vm.tiktok.com/ABC/"]


@pytest.mark.asyncio
async def test_rejects_external_redirect_before_following() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(InvalidURLError):
            await resolve_url("https://vt.tiktok.com/ABC/", client)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_redirect_loop_is_bounded() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(
        302, headers={"location": "/ABC/"},
    ))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(InvalidURLError):
            await resolve_url("https://vm.tiktok.com/ABC/", client)


@pytest.mark.asyncio
async def test_network_errors_are_domain_errors() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(NetworkError):
            await resolve_url("https://vm.tiktok.com/ABC/", client)
