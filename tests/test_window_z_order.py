"""Snapshot lists the windows in z-order, and their Depth is the position in it (front first).

Nothing here reaches a real window: EnumWindows and the other Win32 calls, the virtual desktop
check and the UI Automation elements behind the handles are all fakes.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import windows_mcp.uia as uia
from windows_mcp.desktop.service import Desktop
from windows_mcp.tree.views import TreeState

SERVICE = "windows_mcp.desktop.service"

# Top-level windows in the order EnumWindows reports them: front to back.
Z_ORDER = [
    (0x000A0F12, "Untitled - Notepad"),
    (0x00140C2E, "Inbox - Outlook"),
    (0x0003051A, "Downloads - File Explorer"),
    (0x001B02F4, "service.py - Visual Studio Code"),
    (0x00090DD6, "Settings"),
]


class FakeWindow(uia.WindowControl):
    """An app window with fixed properties and no UI Automation element behind it."""

    def __init__(self, handle: int, name: str):  # Control.__init__ is skipped on purpose.
        self._fake_handle = handle
        self._fake_name = name

    Name = property(lambda self: self._fake_name)
    ClassName = property(lambda self: "FakeWindowClass")
    NativeWindowHandle = property(lambda self: self._fake_handle)
    ProcessId = property(lambda self: 5000)
    BoundingRectangle = property(lambda self: uia.Rect(0, 0, 800, 600))

    def GetPattern(self, pattern_id):
        return SimpleNamespace(CanMinimize=True, CanMaximize=True)

    def __repr__(self):
        return f"FakeWindow({self._fake_handle:#x}, {self._fake_name!r})"


@pytest.fixture
def desktop():
    controls = {handle: FakeWindow(handle, name) for handle, name in Z_ORDER}

    def enum_windows(callback, extra):
        for handle, _ in Z_ORDER:
            callback(handle, extra)

    with (
        patch.object(Desktop, "__init__", lambda self: None),
        patch(f"{SERVICE}.win32gui.EnumWindows", side_effect=enum_windows),
        patch(f"{SERVICE}.win32gui.IsWindow", return_value=True),
        patch(f"{SERVICE}.win32gui.IsWindowVisible", return_value=True),
        patch(f"{SERVICE}.is_window_on_current_desktop", return_value=True),
        patch(f"{SERVICE}.win32gui.FindWindow", return_value=0),
        patch(
            f"{SERVICE}.win32process.GetWindowThreadProcessId",
            return_value=(1, os.getpid() + 1),
        ),
        patch(f"{SERVICE}.uia.ControlFromHandle", side_effect=controls.__getitem__),
        patch(f"{SERVICE}.uia.IsIconic", return_value=False),
        patch(f"{SERVICE}.uia.IsZoomed", return_value=False),
        patch(f"{SERVICE}.uia.IsWindowVisible", return_value=True),
        patch.object(Desktop, "is_window_browser", return_value=False),
        # The front window has the focus.
        patch.object(Desktop, "get_foreground_window", return_value=controls[Z_ORDER[0][0]]),
    ):
        yield Desktop()


def test_windows_are_listed_front_to_back_with_their_depth(desktop):
    windows, handles = desktop.get_windows()

    assert [(window.handle, window.depth) for window in windows] == [
        (handle, depth) for depth, (handle, _) in enumerate(Z_ORDER)
    ]
    assert handles == {handle for handle, _ in Z_ORDER}


def test_focused_window_has_the_depth_of_the_front_window(desktop):
    active_window = desktop.get_active_window()

    assert (active_window.name, active_window.depth) == ("Untitled - Notepad", 0)


def test_snapshot_lists_the_windows_behind_the_focused_one_in_z_order(desktop):
    desktop.tree = MagicMock()
    desktop.tree.get_state.return_value = TreeState()
    desktop.get_displays = MagicMock(return_value=[])
    desktop.get_cursor_location = MagicMock(return_value=(0, 0))

    with (
        patch(f"{SERVICE}.get_current_desktop", return_value={"name": "Desktop 1"}),
        patch(f"{SERVICE}.get_all_desktops", return_value=[{"name": "Desktop 1"}]),
    ):
        state = desktop.get_state(use_vision=False)

    assert (state.active_window.name, state.active_window.depth) == ("Untitled - Notepad", 0)
    assert [(window.name, window.depth) for window in state.windows] == [
        ("Inbox - Outlook", 1),
        ("Downloads - File Explorer", 2),
        ("service.py - Visual Studio Code", 3),
        ("Settings", 4),
    ]
    desktop.tree.get_state.assert_called_once_with(Z_ORDER[0][0], [], use_dom=False)
