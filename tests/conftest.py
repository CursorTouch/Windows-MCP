import os

import pytest

# Disable the post-screenshot flash overlay during the test suite. The overlay
# spawns a Tk window on a daemon thread which races with pytest teardown and
# can crash the interpreter. Tests for the flash itself set/clear this env var
# explicitly via monkeypatch.
os.environ.setdefault("WINDOWS_MCP_DISABLE_FLASH", "1")

from windows_mcp.tree.views import BoundingBox, Center, TreeElementNode, ScrollElementNode
from windows_mcp.desktop.views import Window, Status, DesktopState


@pytest.fixture
def sample_bounding_box():
    return BoundingBox(left=100, top=50, right=300, bottom=150, width=200, height=100)


@pytest.fixture
def sample_center():
    return Center(x=200, y=100)


@pytest.fixture
def sample_tree_element_node(sample_bounding_box, sample_center):
    return TreeElementNode(
        bounding_box=sample_bounding_box,
        center=sample_center,
        name="OK",
        control_type="Button",
        window_name="Notepad",
        metadata={
            "value": "",
            "shortcut": "Alt+O",
            "has_focused": True,
        },
    )


@pytest.fixture
def sample_scroll_element_node(sample_bounding_box, sample_center):
    return ScrollElementNode(
        name="Document",
        control_type="Pane",
        window_name="Notepad",
        bounding_box=sample_bounding_box,
        center=sample_center,
        metadata={
            "horizontal_scrollable": False,
            "horizontal_scroll_percent": 0.0,
            "vertical_scrollable": True,
            "vertical_scroll_percent": 42.5,
            "has_focused": False,
        },
    )


@pytest.fixture
def sample_window(sample_bounding_box):
    return Window(
        name="Untitled - Notepad",
        is_browser=False,
        depth=0,
        status=Status.NORMAL,
        bounding_box=sample_bounding_box,
        handle=12345,
        process_id=6789,
    )


@pytest.fixture
def sample_desktop_state(sample_window):
    return DesktopState(
        active_desktop={"name": "Desktop 1", "id": "abc-123"},
        all_desktops=[
            {"name": "Desktop 1", "id": "abc-123"},
            {"name": "Desktop 2", "id": "def-456"},
        ],
        active_window=sample_window,
        windows=[sample_window],
    )
