import socket

import pytest

from bot.models.exceptions import UnsafeAddressError
from bot.utils.network import public_address, guard_sockets


@pytest.mark.parametrize("address", [
    "127.0.0.1", "::1", "10.0.0.1", "192.168.1.1", "172.16.0.1",
    "169.254.169.254", "169.254.170.2", "100.100.100.200", "168.63.129.16",
    "0.0.0.0", "224.0.0.1", "fc00::1", "fe80::1", "::ffff:127.0.0.1",
])
def test_rejects_private_reserved_and_metadata_addresses(address: str) -> None:
    with pytest.raises(UnsafeAddressError):
        public_address(address)


def test_public_address_is_allowed() -> None:
    assert public_address("8.8.8.8") == "8.8.8.8"


def test_worker_dns_guard_rejects_mixed_answers_and_restores_socket(monkeypatch) -> None:
    def resolver(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    with guard_sockets():
        with pytest.raises(UnsafeAddressError):
            socket.getaddrinfo("cdn.example", 443)
    assert socket.getaddrinfo is resolver


@pytest.mark.asyncio
async def test_transient_dns_errors_are_retryable_network_errors(monkeypatch) -> None:
    import asyncio
    from unittest.mock import AsyncMock
    from bot.models.exceptions import NetworkError
    from bot.services.urls import resolve_url
    from bot.utils.network import public_http_client

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo",
                        AsyncMock(side_effect=socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")))
    async with public_http_client() as client:
        with pytest.raises(NetworkError):
            await resolve_url("https://vm.tiktok.com/ABC/", client)


@pytest.mark.asyncio
async def test_dns_deadline_is_a_connection_timeout(monkeypatch) -> None:
    import asyncio
    import httpcore
    from bot.utils.network import PublicNetworkBackend

    async def stalled(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", stalled)
    with pytest.raises(httpcore.ConnectTimeout):
        await PublicNetworkBackend().connect_tcp("www.tiktok.com", 443, timeout=0.01)


@pytest.mark.asyncio
async def test_http_backend_pins_checked_public_ip(monkeypatch) -> None:
    import asyncio
    from unittest.mock import AsyncMock
    from bot.utils.network import PublicNetworkBackend, AutoBackend

    resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=resolved))
    stream = object()
    connect = AsyncMock(return_value=stream)
    monkeypatch.setattr(AutoBackend, "connect_tcp", connect)
    assert await PublicNetworkBackend().connect_tcp("cdn.example", 443, timeout=1) is stream
    assert connect.call_args.args[0] == "8.8.8.8"


def test_explicit_403_is_permanent_even_with_download_prefix() -> None:
    from bot.models.exceptions import NetworkError, classify_error

    assert not isinstance(classify_error("Unable to download webpage: HTTP Error 403"), NetworkError)
