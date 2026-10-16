"""SSRF protection and IP allowlist utilities for outbound HTTP requests."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import requests
from requests.adapters import DEFAULT_POOLBLOCK, HTTPAdapter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.poolmanager import PoolManager


_FAKE_IP_DNS_RANGES = (
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("2001:2::/48"),
)


def _is_private_target(ip: ipaddress._BaseAddress) -> bool:
    return any((
        ip.is_private,
        ip.is_loopback,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ))


def _is_fake_ip_dns_answer(ip: ipaddress._BaseAddress) -> bool:
    """Whether an address is from a benchmarking range commonly used for Fake-IP DNS."""
    return any(ip in network for network in _FAKE_IP_DNS_RANGES)


def validate_url(url: str, *, allow_private: bool = False) -> str | None:
    """Raise ValueError if the URL is unsafe to fetch (SSRF protection).

    Blocks non-http/https schemes, credential-embedded URLs, and hostnames
    that resolve to private, loopback, link-local, multicast, reserved, or
    unspecified addresses. Pass allow_private=True to skip the address check.

    Returns the address the caller must connect to, or None when no lookup
    was performed. Resolving the hostname a second time at connect would
    reopen the gap this check closes: a DNS record that flips to a private
    address right afterwards would never be seen here.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise ValueError(f"Invalid URL: {url}") from exc

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme '{parsed.scheme}' is not allowed; use http or https.")

    if not parsed.hostname:
        raise ValueError(f"URL has no hostname: {url}")

    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not allowed.")

    if allow_private:
        return None

    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        hostname_is_ip_literal = False
    else:
        hostname_is_ip_literal = True

    try:
        addresses = [
            info[4][0]
            for info in socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
        ]
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname '{parsed.hostname}': {exc}") from exc

    for raw_addr in addresses:
        try:
            ip = ipaddress.ip_address(raw_addr)
        except ValueError:
            raise ValueError(f"Could not validate resolved address: {raw_addr}")
        if _is_private_target(ip):
            if not hostname_is_ip_literal and _is_fake_ip_dns_answer(ip):
                raise ValueError(
                    f"Hostname '{parsed.hostname}' resolved to {ip}, which is in a "
                    "benchmarking range commonly used by proxy or VPN Fake-IP DNS. "
                    "The address remains blocked by SSRF protection. Configure the "
                    "proxy/VPN to return the hostname's real public address or exclude "
                    "this hostname from Fake-IP mode."
                )
            raise ValueError(
                f"Private, loopback, link-local, multicast, and reserved addresses are blocked: {ip}"
            )

    return addresses[0]


class _PinnedConnectionMixin:
    """Connect to a pre-validated address instead of re-resolving the hostname.

    urllib3 takes the Host header and the TLS server name from the same
    attribute it resolves, so the pin is applied only around the socket
    connect and reverted immediately after. The handshake that follows still
    presents — and validates — the original hostname.
    """

    def __init__(self, *args, pinned_ip: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pinned_ip = pinned_ip

    def _new_conn(self):
        if not self._pinned_ip:
            return super()._new_conn()
        hostname = self._dns_host
        self._dns_host = self._pinned_ip
        try:
            return super()._new_conn()
        finally:
            self._dns_host = hostname


class _PinnedHTTPConnection(_PinnedConnectionMixin, HTTPConnection):
    pass


class _PinnedHTTPSConnection(_PinnedConnectionMixin, HTTPSConnection):
    pass


class _PinnedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _PinnedHTTPConnection


class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _PinnedHTTPSConnection


class _PinnedPoolManager(PoolManager):
    """Pool manager whose pools dial one pre-validated address.

    The pin is attached to each pool after it is built rather than passed in
    as a pool argument: urllib3 turns pool arguments into a fixed-shape pool
    key and rejects names it does not know.
    """

    def __init__(self, pinned_ip: str, **kwargs) -> None:
        self._pinned_ip = pinned_ip
        super().__init__(**kwargs)
        self.pool_classes_by_scheme = {
            "http": _PinnedHTTPConnectionPool,
            "https": _PinnedHTTPSConnectionPool,
        }

    def _new_pool(self, scheme, host, port, request_context=None):
        pool = super()._new_pool(scheme, host, port, request_context)
        pool.conn_kw["pinned_ip"] = self._pinned_ip
        return pool


class _PinnedIPAdapter(HTTPAdapter):
    """Requests adapter that routes every connection to one validated address."""

    def __init__(self, pinned_ip: str) -> None:
        self._pinned_ip = pinned_ip
        super().__init__()

    def init_poolmanager(
        self, connections: int, maxsize: int, block: bool = DEFAULT_POOLBLOCK, **pool_kwargs
    ) -> None:
        # Let requests record its own pool bookkeeping, then swap in the
        # manager that applies the pin.
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
        self.poolmanager = _PinnedPoolManager(
            self._pinned_ip,
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )


def safe_get(url: str, *, timeout: float = 10) -> requests.Response:
    """Fetch a URL with SSRF protection, without following redirects.

    Redirects are deliberately left to the caller so that every hop goes
    through validate_url() before it is fetched. Proxied requests bypass the
    pin — requests builds those pools separately — because the connection
    then terminates at the proxy rather than at the validated host.
    """
    pinned_ip = validate_url(url)
    session = requests.Session()
    if pinned_ip:
        adapter = _PinnedIPAdapter(pinned_ip)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    try:
        return session.get(url, timeout=timeout, allow_redirects=False)
    finally:
        session.close()


def parse_ip_allowlist(raw_entries: list[str]) -> list[ipaddress._BaseNetwork]:
    """Parse a list of IP addresses and CIDR ranges into network objects.

    Accepts individual IPs (e.g. '192.168.1.5') and CIDR ranges
    (e.g. '10.0.0.0/8', '2001:db8::/32'). Raises ValueError on invalid entries.
    """
    parsed: list[ipaddress._BaseNetwork] = []
    errors: list[str] = []

    for entry in raw_entries:
        value = entry.strip()
        if not value:
            continue
        try:
            if "/" in value:
                parsed.append(ipaddress.ip_network(value, strict=False))
            else:
                ip = ipaddress.ip_address(value)
                suffix = "/32" if ip.version == 4 else "/128"
                parsed.append(ipaddress.ip_network(f"{ip}{suffix}", strict=False))
        except ValueError as exc:
            errors.append(f"{entry!r}: {exc}")

    if errors:
        raise ValueError("Invalid IP allowlist entries: " + "; ".join(errors))

    return parsed


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """Restrict inbound connections to a configured set of IP networks (CIDR supported)."""

    def __init__(self, app: ASGIApp, *, allowlist: list[ipaddress._BaseNetwork]) -> None:
        super().__init__(app)
        self.allowlist = allowlist

    async def dispatch(self, request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        client = request.client
        if client is None:
            return JSONResponse({"error": "Forbidden: missing client address"}, status_code=403)

        try:
            client_ip = ipaddress.ip_address(client.host)
        except ValueError:
            return JSONResponse(
                {"error": f"Forbidden: invalid client address '{client.host}'"}, status_code=403
            )

        if not any(client_ip in net for net in self.allowlist):
            return JSONResponse(
                {"error": f"Forbidden: {client_ip} is not in the IP allowlist"}, status_code=403
            )

        return await call_next(request)
