"""Tests for parse_ip_allowlist and IPAllowlistMiddleware, including dual-stack IPv4-mapped IPv6."""

from __future__ import annotations

import ipaddress

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from windows_mcp.infrastructure.security import IPAllowlistMiddleware, parse_ip_allowlist


def _make_app(allowlist: list[ipaddress._BaseNetwork]) -> Starlette:
    async def ok(request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/mcp", ok), Route("/health", ok)])
    app.add_middleware(IPAllowlistMiddleware, allowlist=allowlist)
    return app


class TestParseIpAllowlist:
    def test_parses_ipv4_ipv6_and_unwraps_ipv4_mapped(self):
        nets = parse_ip_allowlist([
            "127.0.0.1",
            "192.168.1.0/24",
            "::1",
            "2001:db8::/32",
            "::ffff:10.0.0.5",
            "::ffff:172.16.0.0/112",
            "::ffff:0:0/96",
        ])
        assert nets == [
            ipaddress.IPv4Network("127.0.0.1/32"),
            ipaddress.IPv4Network("192.168.1.0/24"),
            ipaddress.IPv6Network("::1/128"),
            ipaddress.IPv6Network("2001:db8::/32"),
            ipaddress.IPv4Network("10.0.0.5/32"),
            ipaddress.IPv4Network("172.16.0.0/16"),
            ipaddress.IPv4Network("0.0.0.0/0"),
        ]

    def test_preserves_broader_ipv6_prefix(self):
        # ::/0 is IPv6-wide, not an IPv4-mapped range, so it must stay IPv6Network.
        assert parse_ip_allowlist(["::/0"]) == [ipaddress.IPv6Network("::/0")]

    def test_rejects_invalid_entries(self):
        with pytest.raises(ValueError, match="Invalid IP allowlist entries"):
            parse_ip_allowlist(["127.0.0.1", "not-an-ip"])


class TestIPAllowlistMiddleware:
    @pytest.mark.parametrize(
        ("entries", "client_host", "expected_status"),
        [
            (["192.168.1.0/24"], "192.168.1.42", 200),
            (["192.168.1.0/24"], "::ffff:192.168.1.42", 200),
            (["127.0.0.1"], "::ffff:127.0.0.1", 200),
            (["::ffff:192.168.1.0/120"], "192.168.1.42", 200),
            (["::ffff:192.168.1.5"], "192.168.1.5", 200),
            (["::1"], "::1", 200),
            (["2001:db8::/32"], "2001:db8::99", 200),
            (["192.168.1.0/24"], "::ffff:10.0.0.9", 403),
            (["192.168.1.0/24"], "10.0.0.9", 403),
            (["::/0"], "::ffff:203.0.113.5", 403),
            (["127.0.0.1"], "::1", 403),
            (["127.0.0.1"], "not-an-ip", 403),
        ],
    )
    def test_allows_or_blocks_clients(self, entries, client_host, expected_status):
        app = _make_app(parse_ip_allowlist(entries))
        with TestClient(app, client=(client_host, 50000)) as client:
            resp = client.get("/mcp")
        assert resp.status_code == expected_status

    def test_direct_unparsed_ipv4_mapped_network_in_middleware(self):
        # Callers passing raw IPv6Network objects without parse_ip_allowlist still work.
        app = _make_app([ipaddress.ip_network("::ffff:192.168.1.0/120")])
        with TestClient(app, client=("192.168.1.42", 50000)) as client:
            assert client.get("/mcp").status_code == 200

    def test_health_bypasses_allowlist(self):
        app = _make_app(parse_ip_allowlist(["127.0.0.1"]))
        with TestClient(app, client=("::ffff:203.0.113.5", 50000)) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/mcp").status_code == 403
