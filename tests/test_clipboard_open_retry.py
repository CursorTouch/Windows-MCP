"""Clipboard busy handling, with win32clipboard faked (runs on any OS)."""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError


class FakeWin32Error(Exception):
    def __init__(self, winerror, funcname, strerror):
        super().__init__(winerror, funcname, strerror)
        self.winerror = winerror


def make_fake_clipboard(fail_times):
    mod = types.ModuleType("win32clipboard")
    mod.error = FakeWin32Error
    mod.CF_UNICODETEXT = 13
    mod.state = {"opens": 0, "closes": 0, "text": None}

    def open_clipboard():
        mod.state["opens"] += 1
        if mod.state["opens"] <= fail_times:
            raise FakeWin32Error(5, "OpenClipboard", "Access is denied.")

    def close_clipboard():
        mod.state["closes"] += 1

    def set_text(text, fmt):
        mod.state["text"] = text

    mod.OpenClipboard = open_clipboard
    mod.CloseClipboard = close_clipboard
    mod.EmptyClipboard = lambda: None
    mod.SetClipboardText = set_text
    mod.IsClipboardFormatAvailable = lambda fmt: mod.state["text"] is not None
    mod.GetClipboardData = lambda fmt: mod.state["text"]
    return mod


@pytest.fixture
def clipboard_module(monkeypatch):
    if sys.platform != "win32":
        # windows_mcp.infrastructure starts the UIA worker on import; stub what the tool uses.
        infra = types.ModuleType("windows_mcp.infrastructure")
        infra.with_analytics = lambda *a, **k: lambda f: f
        monkeypatch.setitem(sys.modules, "windows_mcp.infrastructure", infra)
        # windows_mcp.tools/__init__ imports every Windows-only tool; load this module alone.
        path = Path(__file__).parents[1] / "src" / "windows_mcp" / "tools" / "clipboard.py"
        spec = importlib.util.spec_from_file_location("_clipboard_under_test", path)
        clipboard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(clipboard)
    else:
        from windows_mcp.tools import clipboard

    monkeypatch.setattr(clipboard.time, "sleep", lambda s: None)
    return clipboard


def run(clipboard, monkeypatch, fake, args):
    monkeypatch.setitem(sys.modules, "win32clipboard", fake)
    mcp = FastMCP("t")
    clipboard.register(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    return asyncio.run(mcp.call_tool("Clipboard", args))


def test_busy_clipboard_retries_then_succeeds(clipboard_module, monkeypatch):
    fake = make_fake_clipboard(fail_times=2)
    run(clipboard_module, monkeypatch, fake, {"mode": "set", "text": "hello"})
    assert fake.state["opens"] == 3 and fake.state["closes"] == 1
    assert fake.state["text"] == "hello"


@pytest.mark.parametrize("mode,args", [("set", {"text": "x"}), ("get", {})])
def test_busy_clipboard_gives_controlled_tool_error(clipboard_module, monkeypatch, mode, args):
    fake = make_fake_clipboard(fail_times=99)
    with pytest.raises(ToolError, match="Clipboard is unavailable \\(error 5\\)"):
        run(clipboard_module, monkeypatch, fake, {"mode": mode, **args})
    assert fake.state["opens"] == clipboard_module.OPEN_ATTEMPTS
    assert fake.state["closes"] == 0  # Never close a clipboard we did not open.


def test_backoff_is_exponential(clipboard_module, monkeypatch):
    delays = []
    monkeypatch.setattr(clipboard_module.time, "sleep", delays.append)
    fake = make_fake_clipboard(fail_times=99)
    with pytest.raises(ToolError):
        run(clipboard_module, monkeypatch, fake, {"mode": "get"})
    base = clipboard_module.OPEN_BASE_DELAY
    assert delays == [base, base * 2, base * 4, base * 8]
