"""Resolve a top-level window by title or PID into a capture rectangle.

Used by the Screenshot/Snapshot tools to support targeting a specific
window without taking a full-desktop screenshot. The resolver is decoupled
from ``Desktop`` so it can be unit-tested without spinning up UIA.
"""

import ctypes
import ctypes.wintypes
import logging

import win32con
import win32gui
import win32process
from fuzzywuzzy import process

import windows_mcp.uia as uia

logger = logging.getLogger(__name__)

DWMWA_EXTENDED_FRAME_BOUNDS = 9
_FUZZY_SCORE_CUTOFF = 70


class WindowNotFoundError(ValueError):
    """Raised when no visible top-level window matches the supplied criteria."""


def enumerate_visible_windows() -> list[tuple[int, str, int]]:
    """Return ``(hwnd, title, pid)`` for every visible, non-cloaked top-level window.

    The list is intended for matching, not display, so untitled windows are
    included for PID-based lookup.
    """
    results: list[tuple[int, str, int]] = []

    def callback(hwnd: int, _: object) -> bool:
        try:
            if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            results.append((hwnd, title, pid))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(callback, None)
    return results


def get_window_rect(hwnd: int) -> uia.Rect:
    """Return the window's frame rect, preferring DWM extended bounds."""
    rect = ctypes.wintypes.RECT()
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.wintypes.HWND(hwnd),
            ctypes.wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
    except Exception:
        hr = 1
    if hr == 0:
        return uia.Rect(rect.left, rect.top, rect.right, rect.bottom)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return uia.Rect(left, top, right, bottom)


def resolve_window(
    *,
    name: str | None = None,
    pid: int | None = None,
    windows: list[tuple[int, str, int]] | None = None,
) -> tuple[int, str]:
    """Resolve a window by exact PID or fuzzy title match.

    PID takes precedence when both are given. Returns ``(hwnd, title)``.
    Raises :class:`WindowNotFoundError` if nothing matches.
    """
    if name is None and pid is None:
        raise ValueError("resolve_window requires either name or pid")

    if windows is None:
        windows = enumerate_visible_windows()

    if pid is not None:
        candidates = [(hwnd, title) for hwnd, title, win_pid in windows if win_pid == pid]
        if not candidates:
            raise WindowNotFoundError(f"No visible window found for PID {pid}")
        # Prefer windows that have a title; fall back to the first match.
        candidates.sort(key=lambda t: 0 if t[1] else 1)
        return candidates[0]

    titled = [(hwnd, title) for hwnd, title, _ in windows if title]
    if not titled:
        raise WindowNotFoundError("No titled windows available for name match")
    titles = [title for _, title in titled]
    match = process.extractOne(name, titles, score_cutoff=_FUZZY_SCORE_CUTOFF)
    if match is None:
        raise WindowNotFoundError(f"No window title matched {name!r} (score cutoff 70)")
    matched_title, _ = match
    for hwnd, title in titled:
        if title == matched_title:
            return hwnd, title
    raise WindowNotFoundError(f"No window title matched {name!r}")


def is_iconic(hwnd: int) -> bool:
    return bool(win32gui.IsIconic(hwnd))


def is_foreground(hwnd: int) -> bool:
    """True if ``hwnd`` is currently the system foreground window."""
    try:
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def restore_if_minimized(hwnd: int) -> None:
    if is_iconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)


def force_foreground(hwnd: int) -> None:
    """Last-resort focus attempt via SwitchToThisWindow.

    ``SetForegroundWindow`` is silently rejected when the calling process
    didn't receive the last input event (Windows foreground lock), even
    after the AttachThreadInput dance. ``SwitchToThisWindow`` is the
    undocumented Win32 API the shell uses for Alt-Tab; it works around the
    lock without injecting keyboard input.
    """
    try:
        ctypes.windll.user32.SwitchToThisWindow(
            ctypes.wintypes.HWND(hwnd), ctypes.wintypes.BOOL(True)
        )
    except Exception:
        logger.debug("SwitchToThisWindow failed for hwnd %s", hwnd, exc_info=True)
