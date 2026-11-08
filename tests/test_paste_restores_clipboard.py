"""Type's paste path must give the clipboard back in every format it held.

Type pastes text of 20 characters or more through the clipboard and then puts
back what was there. It saved the prior clipboard with GetClipboardText(), which
reads "" when the clipboard holds no text, so an image or files copied in
Explorer were replaced by empty text, and copied rich text lost its HTML.

These tests use the real Windows clipboard. The user's clipboard text is put
back afterwards.
"""

import struct
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows clipboard")

if sys.platform == "win32":
    import win32clipboard

    from windows_mcp import uia
    from windows_mcp.desktop import service
    from windows_mcp.desktop.service import Desktop

LONG_TEXT = "typed through the clipboard because it is long"


def _open() -> None:
    # Clipboard history and clipboard managers open the clipboard briefly after
    # each change; retry as uia._OpenClipboard does.
    deadline = time.monotonic() + 2
    while True:
        try:
            win32clipboard.OpenClipboard()
            return
        except win32clipboard.error:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.01)


@pytest.fixture(autouse=True)
def keep_user_clipboard_text():
    _open()
    try:
        saved = (
            win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT)
            else None
        )
    finally:
        win32clipboard.CloseClipboard()
    yield
    if saved is not None:
        _put({win32clipboard.CF_UNICODETEXT: saved})


@pytest.fixture
def pasted(monkeypatch):
    """Run Type's paste path; return the clipboard text each Ctrl+V saw."""
    seen = []

    def send_keys(keys, *args, **kwargs):
        assert keys == "{Ctrl}v"
        seen.append(uia.GetClipboardText())

    monkeypatch.setattr(service.uia, "SendKeys", send_keys)

    def paste(text=LONG_TEXT):
        Desktop.__new__(Desktop)._paste_text(text)
        return seen

    return paste


def _format(name: str) -> int:
    return win32clipboard.RegisterClipboardFormat(name)


def _put(formats: dict) -> None:
    _open()
    try:
        win32clipboard.EmptyClipboard()
        for clipboard_format, data in formats.items():
            win32clipboard.SetClipboardData(clipboard_format, data)
    finally:
        win32clipboard.CloseClipboard()


def _read(clipboard_format):
    _open()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(clipboard_format):
            return None
        return win32clipboard.GetClipboardData(clipboard_format)
    finally:
        win32clipboard.CloseClipboard()


def test_copied_image_is_put_back(pasted):
    # A 2x2 32-bit DIB, and a PNG as browsers put next to it.
    dib = struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 32, 0, 16, 0, 0, 0, 0) + bytes(range(16))
    png = b"\x89PNG\r\n\x1a\n" + b"not a real png body"
    _put({win32clipboard.CF_DIB: dib, _format("PNG"): png})

    assert pasted() == [LONG_TEXT]

    assert _read(win32clipboard.CF_DIB) == dib
    assert _read(_format("PNG")) == png
    assert _read(win32clipboard.CF_UNICODETEXT) is None


def test_copied_files_are_put_back(pasted):
    # What Explorer's Copy puts on the clipboard: a DROPFILES list and the
    # drop effect (1 = copy).
    names = ["C:\\Users\\Public\\a.txt", "C:\\Users\\Public\\b.txt"]
    dropfiles = struct.pack("<IiiII", 20, 0, 0, 0, 1) + ("\0".join(names) + "\0\0").encode(
        "utf-16-le"
    )
    effect = struct.pack("<I", 1)
    _put({win32clipboard.CF_HDROP: dropfiles, _format("Preferred DropEffect"): effect})

    assert pasted() == [LONG_TEXT]

    assert _read(win32clipboard.CF_HDROP) == tuple(names)
    assert _read(_format("Preferred DropEffect")) == effect


def test_copied_rich_text_keeps_its_html(pasted):
    html = b"Version:0.9\r\nStartHTML:-1\r\nEndHTML:-1\r\n<b>bold</b>"
    _put({win32clipboard.CF_UNICODETEXT: "bold", _format("HTML Format"): html})

    assert pasted() == [LONG_TEXT]

    assert _read(win32clipboard.CF_UNICODETEXT) == "bold"
    assert _read(_format("HTML Format")).rstrip(b"\0") == html


def test_clipboard_changed_during_the_paste_is_kept(monkeypatch):
    _put({win32clipboard.CF_UNICODETEXT: "before"})

    def send_keys(keys, *args, **kwargs):
        # The user copies something while Type is pasting.
        _put({win32clipboard.CF_UNICODETEXT: "copied meanwhile"})

    monkeypatch.setattr(service.uia, "SendKeys", send_keys)
    Desktop.__new__(Desktop)._paste_text(LONG_TEXT)

    assert _read(win32clipboard.CF_UNICODETEXT) == "copied meanwhile"
