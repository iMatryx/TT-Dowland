"""SSRF protection at the actual socket boundary, including redirect DNS lookups."""

from contextlib import contextmanager
import ipaddress
import socket
from collections.abc import Iterator

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from bot.models.exceptions import UnsafeAddressError


def public_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError as exc:
        raise UnsafeAddressError("Unsafe network destination: numeric IP required.") from exc
    mapped = getattr(address, "ipv4_mapped", None)
    checked = mapped or address
    if (not checked.is_global or checked.is_multicast or checked.is_reserved
            or checked.is_loopback or checked.is_link_local or checked.is_unspecified
            or str(checked) in {"168.63.129.16"}):
        raise UnsafeAddressError("Unsafe network destination blocked.")
    if isinstance(address, ipaddress.IPv6Address):
        if (address.sixtofour or address.teredo
                or address in ipaddress.ip_network("64:ff9b::/96")
                or address in ipaddress.ip_network("64:ff9b:1::/48")):
            raise UnsafeAddressError("Unsafe network destination: transition address.")
    return str(address)


@contextmanager
def guard_sockets() -> Iterator[None]:
    """Worker-only guard: resolve once, verify every answer, connect only to IPs."""
    original_dns = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def dns(host, port, *args, **kwargs):
        results = original_dns(host, port, *args, **kwargs)
        if not results:
            raise UnsafeAddressError("Unsafe network destination: empty DNS answer.")
        for result in results:
            public_address(result[4][0])
        return results

    def checked(address) -> None:
        if not isinstance(address, tuple) or len(address) < 2 or address[1] not in {80, 443}:
            raise UnsafeAddressError("Unsafe network destination port.")
        public_address(address[0])

    def connect(sock, address):
        checked(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        checked(address)
        return original_connect_ex(sock, address)

    socket.getaddrinfo = dns
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    try:
        yield
    finally:
        socket.getaddrinfo = original_dns
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex


class PublicNetworkBackend(AutoBackend):
    """Pin approved DNS answers for HTTPX while keeping TLS SNI as the hostname."""

    async def connect_tcp(self, host: str, port: int, timeout=None, local_address=None, socket_options=None):
        import asyncio

        if port not in {80, 443}:
            raise UnsafeAddressError("Unsafe network destination port.")
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout(timeout if timeout is not None else 15):
                try:
                    answers = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                except OSError as exc:
                    raise httpcore.ConnectError("DNS resolution failed") from exc
                if not answers:
                    raise UnsafeAddressError("Unsafe network destination: empty DNS answer.")
                addresses = list(dict.fromkeys(public_address(answer[4][0]) for answer in answers))
                last_error = None
                for address in addresses:
                    try:
                        return await super().connect_tcp(address, port, timeout, local_address, socket_options)
                    except (OSError, httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                        last_error = exc
                raise httpcore.ConnectError("All public destinations failed") from last_error
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout("Public connection deadline exceeded") from exc


def public_http_client(*, limit: int = 6) -> httpx.AsyncClient:
    # HTTPX has no public DNS-backend argument. Keep the small httpcore adapter here.
    transport = httpx.AsyncHTTPTransport(trust_env=False, retries=0)
    transport._pool = httpcore.AsyncConnectionPool(
        max_connections=limit, max_keepalive_connections=limit,
        network_backend=PublicNetworkBackend(),
    )
    return httpx.AsyncClient(transport=transport, trust_env=False, timeout=httpx.Timeout(15, connect=10))
