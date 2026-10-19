"""Scrape must decode pages that declare their charset only in the document.

The reported bug: ``Content-Type: text/html`` without a charset makes requests
follow RFC 2616 and decode the body as ISO-8859-1, ignoring the in-document
``<meta charset>``. Non-ASCII pages came back as mojibake -- weather.com.cn,
for instance, answers with a bare ``text/html`` while sending UTF-8.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from windows_mcp.desktop.service import Desktop

UTF8_PAGE = (
    '<html><head><meta charset="utf-8" /><title>哈尔滨天气预报</title></head>'
    "<body>哈尔滨今天天气：多云，13/2℃，西北风3-4级。</body></html>"
)
LATIN1_PAGE = "<html><body>Caf\u00e9 M\u00fcnchen, 20 \u00b0C</body></html>"


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


def _page(payload: bytes, content_type: str) -> Callable[[BaseHTTPRequestHandler], None]:
    """Answer every request with payload -- the Content-Type header is the point here."""

    def handle(request: BaseHTTPRequestHandler) -> None:
        request.send_response(200)
        request.send_header("Content-Type", content_type)
        request.send_header("Content-Length", str(len(payload)))
        request.end_headers()
        request.wfile.write(payload)

    return handle


def _patch_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the loopback fixtures through the SSRF blocklist, as in test_scrape_ssrf.py."""
    monkeypatch.setattr(
        "windows_mcp.infrastructure.security.validate_url",
        lambda url, *, allow_private=False: None,
    )


def _scrape(monkeypatch: pytest.MonkeyPatch, payload: bytes, content_type: str) -> str:
    _patch_validator(monkeypatch)
    with _http_server(_page(payload, content_type)) as base:
        return Desktop.scrape(Desktop.__new__(Desktop), f"{base}/page")


def test_scrape_decodes_utf8_document_when_header_omits_charset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _scrape(monkeypatch, UTF8_PAGE.encode("utf-8"), "text/html")

    assert "哈尔滨今天天气" in content, "UTF-8 page was decoded as ISO-8859-1"


def test_scrape_uses_header_charset_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    content = _scrape(monkeypatch, UTF8_PAGE.encode("utf-8"), "text/html; charset=utf-8")

    assert "哈尔滨今天天气" in content


def test_scrape_keeps_a_declared_latin1_charset(monkeypatch: pytest.MonkeyPatch) -> None:
    content = _scrape(monkeypatch, LATIN1_PAGE.encode("latin-1"), "text/html; charset=ISO-8859-1")

    assert "Café München" in content, "a declared charset must win over sniffing"
