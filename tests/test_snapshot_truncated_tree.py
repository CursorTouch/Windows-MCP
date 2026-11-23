"""Snapshot's UI tree keeps its layout when the capture is truncated.

build_snapshot_response() writes the UI Tree section as an indented template and dedents it
after the tree is filled in. Every tree line below "desktop" starts with the 4 spaces of the
desktop's children, and the dedent removes them together with the template's. The note that
follows a truncated tree starts at column 0, so with it the dedent found no common indent to
remove: the heading, "desktop" and every window and element line kept 4 more spaces than in a
complete capture.

Nothing here touches the real desktop: the window lists, the UI Automation elements and the
nodes of each window are fakes, and no screenshot is taken.
"""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import windows_mcp.__main__ as main_module
from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import Size, Status, Window
from windows_mcp.tree.service import Tree
from windows_mcp.tree.views import (
    BoundingBox,
    SemanticNode,
    TreeElementNode,
    _truncation_note,
)

SERVICE = "windows_mcp.desktop.service"
NOTEPAD = 0x000A0F12  # has the focus
TASKBAR = 0x00010136  # not an app window, so the tree walks it after the focused one


def make_box(left: int, top: int, right: int, bottom: int) -> BoundingBox:
    return BoundingBox(
        left=left, top=top, right=right, bottom=bottom, width=right - left, height=bottom - top
    )


# The window each handle stands for and the one button in it.
WINDOW_BUTTONS = {
    NOTEPAD: ("Untitled - Notepad", "Save", make_box(420, 330, 480, 370)),
    TASKBAR: ("Taskbar", "Start", make_box(0, 1040, 48, 1080)),
}


def get_nodes(
    self: Tree,
    handle: int,
    is_browser: bool = False,
    wait_time: float = 0,
    use_dom: bool = False,
) -> tuple[list[TreeElementNode], list, list, SemanticNode]:
    window_name, button_name, box = WINDOW_BUTTONS[handle]
    self.element_budget.try_consume()
    button = TreeElementNode(
        name=button_name,
        control_type="Button",
        window_name=window_name,
        bounding_box=box,
        center=box.get_center(),
    )
    window = SemanticNode(
        control_type="Window", element_type="window", name=window_name, window_name=window_name
    )
    window.add_child(
        SemanticNode(
            control_type="Button",
            element_type="interactive",
            name=button_name,
            window_name=window_name,
            center=button.center,
            bounding_box=box,
            element=button,
        )
    )
    return [button], [], [], window


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> Iterator[Desktop]:
    monkeypatch.delenv("WINDOWS_MCP_EXCLUDE_PROCESSES", raising=False)
    notepad = Window(
        name="Untitled - Notepad",
        is_browser=False,
        depth=1,
        status=Status.NORMAL,
        bounding_box=make_box(0, 0, 900, 700),
        handle=NOTEPAD,
        process_id=1001,
    )
    with patch.object(Desktop, "__init__", lambda self: None):
        desktop = Desktop()
    desktop.desktop_state = None
    desktop.get_displays = MagicMock(return_value=[])
    desktop.get_screen_size = MagicMock(return_value=Size(width=1920, height=1080))
    desktop.get_screen_box = MagicMock(return_value=make_box(0, 0, 1920, 1080))
    desktop.get_controls_handles = MagicMock(return_value=[TASKBAR, NOTEPAD])
    desktop.get_windows = MagicMock(return_value=([notepad], {NOTEPAD}))
    desktop.get_active_window = MagicMock(return_value=notepad)
    desktop.get_cursor_location = MagicMock(return_value=(0, 0))
    desktop.is_window_browser = MagicMock(return_value=False)
    desktop.tree = Tree(desktop)
    monkeypatch.setattr(main_module, "desktop", desktop)
    with (
        patch(
            "windows_mcp.tree.service.ControlFromHandle",
            side_effect=lambda handle: SimpleNamespace(ClassName="Window"),
        ),
        patch.object(Tree, "get_nodes", get_nodes),
        patch(f"{SERVICE}.get_current_desktop", return_value={"name": "Desktop 1"}),
        patch(f"{SERVICE}.get_all_desktops", return_value=[{"name": "Desktop 1"}]),
    ):
        yield desktop


def snapshot_ui_tree() -> list[str]:
    """Run the Snapshot tool and return the lines of its UI Tree section."""
    text = asyncio.run(main_module.state_tool())[0]
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == "UI Tree:")
    return lines[start:]


def test_complete_tree_starts_at_column_0(
    desktop: Desktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WINDOWS_MCP_MAX_TREE_ELEMENTS", "500")

    assert snapshot_ui_tree() == [
        "UI Tree:",
        "desktop",
        '├── window "Untitled - Notepad"',
        '│   └── [#0] (450,350) button "Save"  [action: click]',
        '└── window "Taskbar"',
        '    └── [#1] (24,1060) button "Start"  [action: click]',
    ]


def test_truncated_tree_keeps_the_layout_of_a_complete_one(
    desktop: Desktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Room for one element: Notepad's button fills it, so the taskbar is not walked.
    monkeypatch.setenv("WINDOWS_MCP_MAX_TREE_ELEMENTS", "1")

    assert snapshot_ui_tree() == [
        "UI Tree:",
        "desktop",
        '└── window "Untitled - Notepad"',
        '    └── [#0] (450,350) button "Save"  [action: click]',
        "",
        _truncation_note(1),
    ]
    assert desktop.desktop_state.tree_state.truncated is True
