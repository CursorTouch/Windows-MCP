"""Unit tests for window resolution used by the Screenshot/Snapshot tools."""

import ctypes
from unittest.mock import MagicMock

import pytest

from windows_mcp.desktop import window_resolver
from windows_mcp.desktop.window_resolver import (
    WindowNotFoundError,
    enumerate_visible_windows,
    get_window_rect,
    resolve_window,
)
from windows_mcp.uia import Rect


def _windows():
    return [
        (101, "Notepad - Untitled", 1000),
        (202, "Cotire — Columbus Time Reporting", 60972),
        (303, "", 60972),
        (404, "Visual Studio Code", 5555),
    ]


class TestEnumerateVisibleWindows:
    def test_filters_invisible_and_invalid(self, monkeypatch):
        def fake_enum(callback, _):
            for hwnd in (1, 2, 3):
                callback(hwnd, None)

        monkeypatch.setattr(window_resolver.win32gui, "EnumWindows", fake_enum)
        monkeypatch.setattr(
            window_resolver.win32gui,
            "IsWindow",
            lambda hwnd: hwnd != 2,
        )
        monkeypatch.setattr(
            window_resolver.win32gui,
            "IsWindowVisible",
            lambda hwnd: hwnd != 3,
        )
        monkeypatch.setattr(
            window_resolver.win32gui,
            "GetWindowText",
            lambda hwnd: f"win-{hwnd}",
        )
        monkeypatch.setattr(
            window_resolver.win32process,
            "GetWindowThreadProcessId",
            lambda hwnd: (0, hwnd * 10),
        )

        results = enumerate_visible_windows()

        assert results == [(1, "win-1", 10)]
        assert 2 not in [r[0] for r in results]
        assert 3 not in [r[0] for r in results]


class TestResolveWindow:
    def test_requires_name_or_pid(self):
        with pytest.raises(ValueError, match="name or pid"):
            resolve_window(windows=_windows())

    def test_resolves_by_pid_prefers_titled(self):
        hwnd, title = resolve_window(pid=60972, windows=_windows())
        assert hwnd == 202
        assert title.startswith("Cotire")

    def test_resolves_by_pid_returns_first_match_when_no_titled(self):
        windows = [
            (1, "", 555),
            (2, "", 555),
        ]
        hwnd, title = resolve_window(pid=555, windows=windows)
        assert hwnd == 1
        assert title == ""

    def test_pid_not_found_raises(self):
        with pytest.raises(WindowNotFoundError, match="PID 999"):
            resolve_window(pid=999, windows=_windows())

    def test_resolves_by_fuzzy_name(self):
        hwnd, title = resolve_window(name="cotire", windows=_windows())
        assert hwnd == 202
        assert title.startswith("Cotire")

    def test_name_not_found_raises(self):
        with pytest.raises(WindowNotFoundError, match="cutoff"):
            resolve_window(name="zzzzzzzzzzz", windows=_windows())

    def test_no_titled_windows_raises(self):
        only_untitled = [(1, "", 1)]
        with pytest.raises(WindowNotFoundError, match="No titled windows"):
            resolve_window(name="anything", windows=only_untitled)

    def test_pid_takes_precedence_over_name(self):
        hwnd, _ = resolve_window(name="visual studio code", pid=60972, windows=_windows())
        assert hwnd == 202


class TestIsForeground:
    def test_true_when_foreground_handle_matches(self, monkeypatch):
        monkeypatch.setattr(window_resolver.win32gui, "GetForegroundWindow", lambda: 4242)
        assert window_resolver.is_foreground(4242) is True

    def test_false_when_foreground_handle_differs(self, monkeypatch):
        monkeypatch.setattr(window_resolver.win32gui, "GetForegroundWindow", lambda: 999)
        assert window_resolver.is_foreground(4242) is False

    def test_false_when_call_raises(self, monkeypatch):
        def boom():
            raise OSError("denied")

        monkeypatch.setattr(window_resolver.win32gui, "GetForegroundWindow", boom)
        assert window_resolver.is_foreground(1) is False


class TestForceForeground:
    def test_invokes_switch_to_this_window(self, monkeypatch):
        calls: list[tuple] = []
        fake_user32 = MagicMock()
        fake_user32.SwitchToThisWindow.side_effect = lambda hwnd, fAlt: calls.append(
            (hwnd.value, fAlt.value)
        )
        monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))
        window_resolver.force_foreground(7777)
        assert calls and calls[0] == (7777, True)

    def test_swallows_exception(self, monkeypatch):
        fake_user32 = MagicMock()
        fake_user32.SwitchToThisWindow.side_effect = OSError("nope")
        monkeypatch.setattr(ctypes, "windll", MagicMock(user32=fake_user32))
        window_resolver.force_foreground(1)


class TestGetWindowRect:
    def test_uses_dwm_when_call_succeeds(self, monkeypatch):
        captured = {}

        def fake_dwm(hwnd, attr, rect_ptr, size):
            captured["hwnd"] = hwnd.value
            r = ctypes.cast(rect_ptr, ctypes.POINTER(ctypes.wintypes.RECT)).contents
            r.left, r.top, r.right, r.bottom = 100, 200, 600, 700
            return 0

        fake_dwmapi = MagicMock()
        fake_dwmapi.DwmGetWindowAttribute.side_effect = fake_dwm
        monkeypatch.setattr(ctypes, "windll", MagicMock(dwmapi=fake_dwmapi))

        rect = get_window_rect(12345)

        assert isinstance(rect, Rect)
        assert (rect.left, rect.top, rect.right, rect.bottom) == (100, 200, 600, 700)
        assert captured["hwnd"] == 12345

    def test_falls_back_to_get_window_rect_when_dwm_fails(self, monkeypatch):
        fake_dwmapi = MagicMock()
        fake_dwmapi.DwmGetWindowAttribute.return_value = 1  # nonzero HRESULT
        monkeypatch.setattr(ctypes, "windll", MagicMock(dwmapi=fake_dwmapi))
        monkeypatch.setattr(
            window_resolver.win32gui,
            "GetWindowRect",
            lambda hwnd: (10, 20, 30, 40),
        )

        rect = get_window_rect(99)
        assert (rect.left, rect.top, rect.right, rect.bottom) == (10, 20, 30, 40)

    def test_falls_back_when_dwm_raises(self, monkeypatch):
        fake_dwmapi = MagicMock()
        fake_dwmapi.DwmGetWindowAttribute.side_effect = OSError("oops")
        monkeypatch.setattr(ctypes, "windll", MagicMock(dwmapi=fake_dwmapi))
        monkeypatch.setattr(
            window_resolver.win32gui,
            "GetWindowRect",
            lambda hwnd: (1, 2, 3, 4),
        )

        rect = get_window_rect(1)
        assert (rect.left, rect.top, rect.right, rect.bottom) == (1, 2, 3, 4)


class TestResolveWindowCaptureRectFocusFailure:
    """Regression test for PR #233 review feedback: when focus=True and the
    post-condition foreground check fails for both bring_window_to_top and
    force_foreground, the method must raise instead of capturing wrong content.
    bring_window_to_top swallows its own exceptions, so the try/except path is
    not a reliable failure signal — only the explicit ``is_foreground`` check is.
    """

    def test_raises_when_window_never_becomes_foreground(self, monkeypatch):
        from windows_mcp.desktop.service import Desktop

        # Stand up a Desktop without running its full __init__ (Tree/UIA setup
        # would otherwise pull in heavy Windows COM state in a unit test).
        desktop = Desktop.__new__(Desktop)

        # Stub Desktop.bring_window_to_top so it neither raises nor focuses
        # — same as real-world failure (it logs and returns).
        monkeypatch.setattr(Desktop, "bring_window_to_top", lambda self, hwnd: None)

        # Resolver returns a fake (hwnd, title).
        monkeypatch.setattr(
            window_resolver, "resolve_window", lambda name=None, pid=None: (12345, "Stub Title")
        )
        # is_foreground always returns False — focus attempt "fails".
        monkeypatch.setattr(window_resolver, "is_foreground", lambda hwnd: False)
        # force_foreground does nothing (also a no-op).
        monkeypatch.setattr(window_resolver, "force_foreground", lambda hwnd: None)
        # Make sleep a no-op so the test is fast.
        monkeypatch.setattr("windows_mcp.desktop.service.sleep", lambda s: None)

        with pytest.raises(WindowNotFoundError) as excinfo:
            desktop.resolve_window_capture_rect(name="Stub")
        assert "foreground" in str(excinfo.value).lower()
        assert "focus_window=False" in str(excinfo.value)

    def test_returns_rect_when_force_foreground_succeeds(self, monkeypatch):
        from windows_mcp.desktop.service import Desktop

        desktop = Desktop.__new__(Desktop)

        monkeypatch.setattr(Desktop, "bring_window_to_top", lambda self, hwnd: None)
        monkeypatch.setattr(
            window_resolver, "resolve_window", lambda name=None, pid=None: (12345, "Stub Title")
        )
        # First check fails, second (after force_foreground) succeeds.
        calls = {"is_foreground": 0}

        def fake_is_foreground(hwnd):
            calls["is_foreground"] += 1
            return calls["is_foreground"] >= 2

        monkeypatch.setattr(window_resolver, "is_foreground", fake_is_foreground)
        monkeypatch.setattr(window_resolver, "force_foreground", lambda hwnd: None)
        monkeypatch.setattr("windows_mcp.desktop.service.sleep", lambda s: None)
        monkeypatch.setattr(
            window_resolver, "get_window_rect", lambda hwnd: Rect(10, 20, 110, 120)
        )

        rect, title = desktop.resolve_window_capture_rect(name="Stub")
        assert title == "Stub Title"
        assert (rect.left, rect.top, rect.right, rect.bottom) == (10, 20, 110, 120)
