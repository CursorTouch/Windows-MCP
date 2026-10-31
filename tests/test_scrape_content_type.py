"""Scrape must return non-HTML bodies as fetched.

The reported bug: every body went through markdownify, which reads plain text
as a single HTML text node. JSON, source files and text lost their line breaks
and indentation, and "_" / "*" came back backslash-escaped.
"""

import pytest
from markdownify import markdownify

from tests.test_scrape_encoding import _scrape

PYTHON_SOURCE = "def add(a, b):\n    return a * b  # snake_case_name\n\nprint(add(2, 3))\n"
JSON_BODY = '{\n  "user_name": "a_b",\n  "glob": "*.py"\n}\n'
HTML_PAGE = "<html><body><h1>Title</h1><p>snake_case <b>bold</b></p></body></html>"


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (PYTHON_SOURCE, "text/plain; charset=utf-8"),
        (PYTHON_SOURCE, "text/x-python"),
        (JSON_BODY, "application/json"),
        (JSON_BODY, "application/json; charset=utf-8"),
    ],
    ids=["text-plain", "text-x-python", "json", "json-charset"],
)
def test_scrape_returns_non_html_body_verbatim(
    monkeypatch: pytest.MonkeyPatch, body: str, content_type: str
) -> None:
    content = _scrape(monkeypatch, body.encode("utf-8"), content_type)

    assert content == body


@pytest.mark.parametrize(
    "content_type", ["text/html", "TEXT/HTML; charset=utf-8", "application/xhtml+xml"]
)
def test_scrape_still_converts_html_to_markdown(
    monkeypatch: pytest.MonkeyPatch, content_type: str
) -> None:
    content = _scrape(monkeypatch, HTML_PAGE.encode("utf-8"), content_type)

    assert content == markdownify(html=HTML_PAGE)
    assert "**bold**" in content
