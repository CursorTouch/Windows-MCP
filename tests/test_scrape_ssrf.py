"""SSRF protection for the Scrape tool (GHSA-6v5g-vq9r-jrff).

The reported bug: scrape() validated only the caller-supplied URL and then let
requests follow 3xx redirects on its own, so a public URL could bounce the
fetch onto a loopback or link-local address that validate_url() would have
rejected outright. The same class of bypass also applies to DNS: a record that
changes between the check and the connect, which is why safe_get() pins the
address it validated.
"""

import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from windows_mcp.desktop.service import Desktop
from windows_mcp.infrastructure import safe_get, validate_url


@contextmanager
def _http_server(handle_get: Callable[[BaseHTTPRequestHandler], None]) -> Iterator[str]:
    """Run handle_get as a loopback HTTP server and yield its base URL."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler naming
            handle_get(self)

        def log_message(self, fmt, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _body(text: str) -> Callable[[BaseHTTPRequestHandler], None]:
    def handle(request: BaseHTTPRequestHandler) -> None:
        request.send_response(200)
        request.end_headers()
        request.wfile.write(text.encode())

    return handle


def _redirect(location: str) -> Callable[[BaseHTTPRequestHandler], None]:
    def handle(request: BaseHTTPRequestHandler) -> None:
        request.send_response(302)
        request.send_header("Location", location)
        request.end_headers()

    return handle


def _patch_validator(monkeypatch, blocked: set[str]) -> list[str]:
    """Replace the SSRF check with a recording stand-in.

    Every URL in this test lives on loopback, so the real blocklist would
    reject the entry point too. The stand-in keeps the shape of the real
    policy — some URLs pass, the internal target does not — and records the
    hops so a test can assert the redirect target was actually checked.
    """
    seen: list[str] = []

    def fake_validate_url(url: str, *, allow_private: bool = False) -> None:
        seen.append(url)
        if url in blocked:
            raise ValueError(
                f"Private, loopback, link-local, multicast, and reserved addresses are blocked: {url}"
            )

    monkeypatch.setattr("windows_mcp.infrastructure.security.validate_url", fake_validate_url)
    return seen


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:7777/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/admin",
        "http://[::1]/secret",
        "http://100.100.100.200/latest/meta-data/",
        "http://100.64.0.1/admin",
        "http://[::ffff:100.100.100.200]/latest/meta-data/",
    ],
)
def test_validate_url_blocks_internal_targets(url: str) -> None:
    with pytest.raises(ValueError, match="blocked"):
        validate_url(url)


@pytest.mark.parametrize("url", ["http://100.63.255.255/", "http://100.128.0.0/"])
def test_validate_url_allows_public_neighbors_of_cgnat(url: str) -> None:
    assert validate_url(url) == url.split("/")[2]


@pytest.mark.parametrize("url", ["file:///C:/Windows/win.ini", "gopher://example.com/"])
def test_validate_url_blocks_non_http_schemes(url: str) -> None:
    with pytest.raises(ValueError, match="not allowed"):
        validate_url(url)


def test_scrape_refuses_redirect_to_blocked_target(monkeypatch) -> None:
    hits: list[str] = []

    def internal(request: BaseHTTPRequestHandler) -> None:
        hits.append(request.path)
        _body("INTERNAL_SECRET=ssrf-ok")(request)

    with _http_server(internal) as internal_base:
        secret_url = f"{internal_base}/secret"
        with _http_server(_redirect(secret_url)) as attacker_base:
            entry_url = f"{attacker_base}/redirect"
            seen = _patch_validator(monkeypatch, blocked={secret_url})

            with pytest.raises(ValueError, match="blocked"):
                Desktop.scrape(Desktop.__new__(Desktop), entry_url)

    assert seen == [entry_url, secret_url], "each hop must be validated before it is fetched"
    assert hits == [], "the blocked internal service must never be contacted"


def test_scrape_follows_allowed_redirect(monkeypatch) -> None:
    with _http_server(_body("<html><body>allowed-content</body></html>")) as target_base:
        target_url = f"{target_base}/page"
        with _http_server(_redirect(target_url)) as entry_base:
            entry_url = f"{entry_base}/redirect"
            seen = _patch_validator(monkeypatch, blocked=set())

            content = Desktop.scrape(Desktop.__new__(Desktop), entry_url)

    assert "allowed-content" in content
    assert seen == [entry_url, target_url]


def test_scrape_validates_relative_redirect_against_current_url(monkeypatch) -> None:
    with _http_server(_redirect("/secret")) as entry_base:
        entry_url = f"{entry_base}/redirect"
        secret_url = f"{entry_base}/secret"
        seen = _patch_validator(monkeypatch, blocked={secret_url})

        with pytest.raises(ValueError, match="blocked"):
            Desktop.scrape(Desktop.__new__(Desktop), entry_url)

    assert seen == [entry_url, secret_url]


def test_scrape_stops_after_redirect_limit(monkeypatch) -> None:
    hops: list[str] = []

    def bouncing(request: BaseHTTPRequestHandler) -> None:
        hops.append(request.path)
        _redirect(f"/hop{len(hops)}")(request)

    with _http_server(bouncing) as base:
        _patch_validator(monkeypatch, blocked=set())

        with pytest.raises(ValueError, match="Too many redirects"):
            Desktop.scrape(Desktop.__new__(Desktop), f"{base}/start")

    assert len(hops) == 5


def _addrinfo(ip: str, port: int) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


def test_validate_url_returns_the_address_to_connect_to(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34", 80))

    assert validate_url("http://public.example/page") == "93.184.216.34"


def test_safe_get_ignores_dns_that_changes_after_validation(monkeypatch) -> None:
    """A record that flips to another address post-check must not be honoured.

    The fake resolver hands out the reachable loopback server once — for the
    validation lookup — and an unroutable TEST-NET-3 address from then on. A
    connect that re-resolves the hostname lands on TEST-NET-3 and fails; a
    connect pinned to the validated address reaches the server.
    """
    seen_hosts: list[str] = []

    def record_host(request: BaseHTTPRequestHandler) -> None:
        seen_hosts.append(request.headers["Host"])
        _body("<html><body>pinned-content</body></html>")(request)

    with _http_server(record_host) as base:
        port = int(base.rsplit(":", 1)[1])
        real_getaddrinfo = socket.getaddrinfo
        lookups: list[str] = []

        def rebinding_getaddrinfo(host, port_, *args, **kwargs):
            if host != "rebind.example":
                return real_getaddrinfo(host, port_, *args, **kwargs)
            lookups.append(host)
            return _addrinfo("127.0.0.1" if len(lookups) == 1 else "203.0.113.9", port_)

        monkeypatch.setattr(socket, "getaddrinfo", rebinding_getaddrinfo)
        monkeypatch.setattr(
            "windows_mcp.infrastructure.security._is_private_target", lambda ip: False
        )

        response = safe_get(f"http://rebind.example:{port}/page", timeout=5)

    assert response.status_code == 200
    assert "pinned-content" in response.text
    assert seen_hosts == [f"rebind.example:{port}"], "Host header must survive the pin"


def test_safe_get_still_works_through_a_proxy(monkeypatch) -> None:
    """Pinning must not hijack proxied requests, which end at the proxy."""
    proxied_paths: list[str] = []

    def proxy(request: BaseHTTPRequestHandler) -> None:
        proxied_paths.append(request.path)
        _body("<html><body>via-proxy</body></html>")(request)

    with _http_server(proxy) as proxy_base:
        real_getaddrinfo = socket.getaddrinfo

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host != "proxied.example":
                return real_getaddrinfo(host, port, *args, **kwargs)
            return _addrinfo("93.184.216.34", port or 80)

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        monkeypatch.setenv("HTTP_PROXY", proxy_base)
        monkeypatch.setenv("NO_PROXY", "")

        response = safe_get("http://proxied.example/page", timeout=5)

    assert response.status_code == 200
    assert "via-proxy" in response.text
    assert proxied_paths == ["http://proxied.example/page"]
