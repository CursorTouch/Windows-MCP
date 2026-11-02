"""Snapshot's UI tree walks the windows other than the focused one front to back.

get_controls_handles() lists the top-level windows in z-order, front first. The tree walks the
windows in the order it gets them, so that order is the order of their elements, and once the
element limit is reached, the windows after it are skipped.

Nothing here reaches a real window: the window lists, the UI Automation elements and the nodes
of each window are fakes, and no screenshot is taken.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import Size, Status, Window
from windows_mcp.tree.service import Tree
from windows_mcp.tree.views import BoundingBox, Center, TreeElementNode, TreeState

SERVICE = "windows_mcp.desktop.service"


def make_box(left: int, top: int, right: int, bottom: int) -> BoundingBox:
    return BoundingBox(
        left=left, top=top, right=right, bottom=bottom, width=right - left, height=bottom - top
    )


# Top-level windows front to back, as get_controls_handles() returns them. The app windows, which
# get_windows() lists, have a bounding box. The taskbar, the dialog and the desktop are not app
# windows, so get_state() passes them to the tree with or without a region.
Z_ORDER = [
    (0x00010136, "Taskbar", None),
    (0x000A0F12, "Untitled - Notepad", make_box(0, 0, 900, 700)),
    (0x00140C2E, "Inbox - Outlook", make_box(1000, 50, 1900, 900)),
    (0x00340E8C, "Find", None),
    (0x0003051A, "Downloads - File Explorer", make_box(1100, 100, 1800, 800)),
    (0x001B02F4, "service.py - Visual Studio Code", make_box(20, 20, 940, 1000)),
    (0x00090DD6, "Settings", make_box(1200, 200, 1700, 700)),
    (0x00010150, "Program Manager", None),
]
NAMES = {handle: name for handle, name, _ in Z_ORDER}
NOTEPAD = 0x000A0F12  # has the focus
# The right half of the screen: Outlook, File Explorer and Settings are in it, Notepad and VS Code
# are not.
REGION = [960, 0, 1920, 1040]


@pytest.fixture
def desktop():
    windows = [
        Window(
            name=name,
            is_browser=False,
            depth=depth,
            status=Status.NORMAL,
            bounding_box=box,
            handle=handle,
            process_id=1000 + depth,
        )
        for depth, (handle, name, box) in enumerate(Z_ORDER)
        if box is not None
    ]
    with patch.object(Desktop, "__init__", lambda self: None):
        desktop = Desktop()
    desktop.tree = MagicMock()
    desktop.tree.get_state.return_value = TreeState()
    desktop.get_displays = MagicMock(return_value=[])
    desktop.get_screen_box = MagicMock(return_value=make_box(0, 0, 1920, 1080))
    desktop.get_controls_handles = MagicMock(return_value=[handle for handle, _, _ in Z_ORDER])
    desktop.get_windows = MagicMock(return_value=(windows, {window.handle for window in windows}))
    desktop.get_active_window = MagicMock(
        return_value=next(window for window in windows if window.handle == NOTEPAD)
    )
    desktop.get_cursor_location = MagicMock(return_value=(0, 0))
    with (
        patch(f"{SERVICE}.get_current_desktop", return_value={"name": "Desktop 1"}),
        patch(f"{SERVICE}.get_all_desktops", return_value=[{"name": "Desktop 1"}]),
    ):
        yield desktop


def test_tree_gets_the_other_windows_front_to_back(desktop):
    desktop.get_state(use_vision=False)

    desktop.tree.get_state.assert_called_once()
    active_handle, other_handles = desktop.tree.get_state.call_args.args[:2]
    assert active_handle == NOTEPAD
    assert [NAMES[handle] for handle in other_handles] == ["Taskbar", "Find", "Program Manager"]


def test_tree_gets_the_windows_in_a_region_front_to_back(desktop):
    desktop.get_state(use_vision=False, region=REGION)

    desktop.tree.get_state.assert_called_once()
    active_handle, other_handles = desktop.tree.get_state.call_args.args[:2]
    # Notepad is outside the region, so the tree walks only the other windows.
    assert active_handle is None
    assert [NAMES[handle] for handle in other_handles] == [
        "Taskbar",
        "Inbox - Outlook",
        "Find",
        "Downloads - File Explorer",
        "Settings",
        "Program Manager",
    ]


def test_element_limit_skips_the_windows_at_the_back(desktop, monkeypatch):
    # Each window has one element, and the limit leaves room for three.
    monkeypatch.setenv("WINDOWS_MCP_MAX_TREE_ELEMENTS", "3")
    monkeypatch.delenv("WINDOWS_MCP_EXCLUDE_PROCESSES", raising=False)
    desktop.get_screen_size = MagicMock(return_value=Size(width=1920, height=1080))
    desktop.is_window_browser = MagicMock(return_value=False)
    desktop.tree = Tree(desktop)

    def get_nodes(self, handle, is_browser=False, wait_time=0, use_dom=False):
        self.element_budget.try_consume()
        button = TreeElementNode(
            name="OK",
            control_type="ButtonControl",
            window_name=NAMES[handle],
            bounding_box=make_box(1400, 500, 1480, 540),
            center=Center(x=1440, y=520),
        )
        return [button], [], [], None

    with (
        patch(
            "windows_mcp.tree.service.ControlFromHandle",
            side_effect=lambda handle: SimpleNamespace(ClassName="Window"),
        ),
        patch.object(Tree, "get_nodes", get_nodes),
    ):
        state = desktop.get_state(use_vision=False, region=REGION)

    # The three windows in front are kept. File Explorer, Settings and the desktop are skipped.
    assert [node.window_name for node in state.tree_state.interactive_nodes] == [
        "Taskbar",
        "Inbox - Outlook",
        "Find",
    ]
    assert state.tree_state.truncated is True
