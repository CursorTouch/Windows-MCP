"""A click that lands on a disabled window must not report a plain success.

Windows drops mouse input aimed at a window disabled by a modal dialog, so
Click answered "Single left clicked at (124,72)." while nothing happened and
the agent went on from a wrong state (#557).
"""

import asyncio
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from windows_mcp.desktop import service
from windows_mcp.desktop.service import Desktop
from windows_mcp.tools.input import register

SINGLE_MONITOR = (0, 0, 1920, 1080)
MAIN_WINDOW = 0x1234
DIALOG_WINDOW = 0x5678


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> Desktop:
    """A Desktop that clicks for real but never touches this machine's windows."""
    instance = Desktop.__new__(Desktop)
    instance.desktop_state = None
    owner = MagicMock()
    owner.checkpoint_current.return_value = None
    owner.record_step_current.return_value = None
    monkeypatch.setattr(service, "get_controller", lambda: owner)
    monkeypatch.setattr(service.uia, "GetVirtualScreenRect", lambda: SINGLE_MONITOR)
    monkeypatch.setattr(service.uia, "Click", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.control_overlay, "ripple", lambda x, y: None)
    return instance


def _window_at(monkeypatch: pytest.MonkeyPatch, enabled: bool, title: str = "") -> None:
    """Answer the Win32 queries as if the point sat on one window of a dialog owner."""
    monkeypatch.setattr(service.win32gui, "WindowFromPoint", lambda point: MAIN_WINDOW)
    monkeypatch.setattr(service.win32gui, "GetAncestor", lambda handle, flag: MAIN_WINDOW)
    monkeypatch.setattr(service.win32gui, "IsWindowEnabled", lambda handle: enabled)
    popup = DIALOG_WINDOW if title else 0
    monkeypatch.setattr(service.win32gui, "GetWindow", lambda handle, index: popup)
    monkeypatch.setattr(
        service.win32gui, "GetWindowText", lambda handle: title if handle == DIALOG_WINDOW else ""
    )


def test_a_click_on_a_live_window_adds_no_warning(
    desktop: Desktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    _window_at(monkeypatch, enabled=True)

    assert desktop.click(loc=[100, 200]) == ""


def test_a_click_on_a_disabled_window_names_the_dialog(
    desktop: Desktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    _window_at(monkeypatch, enabled=False, title="Sign in")

    warning = desktop.click(loc=[100, 200])

    assert "disabled" in warning
    assert "Sign in" in warning


def test_a_click_on_a_disabled_window_without_a_popup_still_warns(
    desktop: Desktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    _window_at(monkeypatch, enabled=False)

    warning = desktop.click(loc=[100, 200])

    assert "modal dialog" in warning
    assert "''" not in warning


def test_a_failing_window_lookup_does_not_break_the_click(
    desktop: Desktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The click already happened; a Win32 hiccup while explaining it must not surface."""

    def boom(*args: object) -> None:
        raise OSError("window is gone")

    monkeypatch.setattr(service.win32gui, "WindowFromPoint", boom)

    assert desktop.click(loc=[100, 200]) == ""


def test_hover_only_reports_no_warning(desktop: Desktop, monkeypatch: pytest.MonkeyPatch) -> None:
    """clicks=0 only moves the pointer, so nothing was dropped."""
    _window_at(monkeypatch, enabled=False, title="Sign in")

    assert desktop.click(loc=[100, 200], clicks=0) == ""


def test_the_click_tool_repeats_the_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = MagicMock()
    desktop.click.return_value = "Warning: the window under the point is disabled."
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)

    answer = asyncio.run(mcp.tools["Click"](loc=[10, 20]))

    assert answer == (
        "Single left clicked at (10,20). Warning: the window under the point is disabled."
    )
