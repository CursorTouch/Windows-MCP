"""App resize without a name acts on the window in the foreground when it is called.

It used to take the active window of the last Snapshot: after an App switch or launch
that was another window, and before any capture or after a Screenshot (which does not
list windows) there was none, so the call answered "No active window found".
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.desktop import service
from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import DesktopState, Status, Window
from windows_mcp.tools import app
from windows_mcp.tree.views import BoundingBox


def _window(name: str, handle: int, status: Status = Status.NORMAL) -> Window:
    return Window(
        name=name,
        is_browser=False,
        depth=0,
        status=status,
        bounding_box=BoundingBox(left=100, top=100, right=900, bottom=700, width=800, height=600),
        handle=handle,
        process_id=handle,
    )


NOTEPAD = _window("Untitled - Notepad", 100)
OUTLOOK = _window("Inbox - Outlook", 200)


def _state(active_window: Window | None, windows: list[Window]) -> DesktopState:
    return DesktopState(
        active_desktop={"id": "1", "name": "Desktop 1"},
        all_desktops=[{"id": "1", "name": "Desktop 1"}],
        active_window=active_window,
        windows=windows,
    )


@pytest.fixture
def desktop(monkeypatch):
    """A Desktop with faked windows and foreground window that records MoveWindow calls."""
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = None
    desktop.get_state = Mock(side_effect=AssertionError("get_state walks the UI tree"))
    desktop.get_windows = Mock(return_value=([NOTEPAD, OUTLOOK], {100, 200}))
    desktop.foreground = SimpleNamespace(ClassName="Window", NativeWindowHandle=NOTEPAD.handle)
    desktop.get_foreground_window = lambda: desktop.foreground
    desktop.moved = []

    def control_from_handle(handle):
        def move_window(x, y, width, height):
            desktop.moved.append((handle, x, y, width, height))

        rect = SimpleNamespace(left=100, top=100, width=lambda: 800, height=lambda: 600)
        return SimpleNamespace(BoundingRectangle=rect, MoveWindow=move_window)

    monkeypatch.setattr(service.uia, "ControlFromHandle", control_from_handle)
    return desktop


@pytest.mark.parametrize(
    "last_capture",
    [
        pytest.param(None, id="no-capture-yet"),
        # Screenshot skips window enumeration, so its state has no active window.
        pytest.param(_state(None, []), id="after-screenshot"),
        # Snapshot saw Notepad in front; App switch then brought Outlook forward.
        pytest.param(_state(NOTEPAD, [OUTLOOK]), id="after-snapshot-then-switch"),
    ],
)
def test_resize_without_name_resizes_the_foreground_window(desktop, last_capture):
    desktop.desktop_state = last_capture
    desktop.foreground.NativeWindowHandle = OUTLOOK.handle

    assert desktop.resize_app(size=(640, 480)) == (
        "Inbox - Outlook resized to 640x480 at 100,100.",
        0,
    )
    assert desktop.moved == [(OUTLOOK.handle, 100, 100, 640, 480)]
    desktop.get_state.assert_not_called()


def test_resize_without_name_uses_the_current_window_status(desktop):
    # Snapshot saw Notepad maximized; it has been restored since.
    desktop.desktop_state = _state(_window(NOTEPAD.name, 100, Status.MAXIMIZED), [OUTLOOK])

    assert desktop.resize_app(loc=(0, 0)) == ("Untitled - Notepad resized to 800x600 at 0,0.", 0)
    assert desktop.moved == [(NOTEPAD.handle, 0, 0, 800, 600)]


def test_resize_without_name_refuses_a_window_minimized_since_the_capture(desktop):
    desktop.desktop_state = _state(NOTEPAD, [OUTLOOK])
    desktop.get_windows.return_value = (
        [_window(NOTEPAD.name, 100, Status.MINIMIZED), OUTLOOK],
        {100, 200},
    )

    assert desktop.resize_app(size=(640, 480)) == ("Untitled - Notepad is minimized", 1)
    assert desktop.moved == []


def test_resize_without_name_reports_no_window_when_the_desktop_is_in_front(desktop):
    desktop.desktop_state = _state(NOTEPAD, [OUTLOOK])
    desktop.foreground = SimpleNamespace(ClassName="Progman", NativeWindowHandle=1)

    assert desktop.resize_app(size=(640, 480)) == ("No active window found", 1)
    assert desktop.moved == []


@pytest.mark.asyncio
async def test_app_tool_resizes_the_foreground_window_after_a_screenshot(desktop):
    desktop.desktop_state = _state(None, [])
    server = FastMCP("app-resize-test")
    app.register(server, get_desktop=lambda: desktop, get_analytics=lambda: None)

    async with Client(server) as client:
        result = await client.call_tool("App", {"mode": "resize", "window_size": [640, 480]})

    assert result.content[0].text == "Untitled - Notepad resized to 640x480 at 100,100."
    assert desktop.moved == [(NOTEPAD.handle, 100, 100, 640, 480)]
