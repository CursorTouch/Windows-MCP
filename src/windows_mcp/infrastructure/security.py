"""SSRF protection and IP allowlist utilities for outbound HTTP requests."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp


def _is_private_target(ip: ipaddress._BaseAddress) -> bool:
    return any((
        ip.is_private,
        ip.is_loopback,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ))


def validate_url(url: str, *, allow_private: bool = False) -> None:
    """Raise ValueError if the URL is unsafe to fetch (SSRF protection).

    Blocks non-http/https schemes, credential-embedded URLs, and hostnames
    that resolve to private, loopback, link-local, multicast, reserved, or
    unspecified addresses. Pass allow_private=True to skip the address check.
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
        return

    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname '{parsed.hostname}': {exc}") from exc

    for raw_addr in addresses:
        try:
            ip = ipaddress.ip_address(raw_addr)
        except ValueError:
            raise ValueError(f"Could not validate resolved address: {raw_addr}")
        if _is_private_target(ip):
            raise ValueError(
                f"Private, loopback, link-local, multicast, and reserved addresses are blocked: {ip}"
            )


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
