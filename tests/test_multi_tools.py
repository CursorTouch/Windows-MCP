import asyncio
from unittest.mock import MagicMock

from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import DesktopState
from windows_mcp.tree.views import BoundingBox, Center, ScrollElementNode, TreeElementNode, TreeState
from windows_mcp.tools.multi import register


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *, name, **kwargs):
        def decorator(func):
            self.tools[name] = func
            return func

        return decorator


def make_desktop_with_tree_state():
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = DesktopState(
        active_desktop={"name": "Desktop 1"},
        all_desktops=[],
        active_window=None,
        windows=[],
        tree_state=TreeState(
            interactive_nodes=[
                TreeElementNode(
                    bounding_box=BoundingBox(left=0, top=0, right=20, bottom=20, width=20, height=20),
                    center=Center(x=10, y=10),
                    name="Button 1",
                    control_type="Button",
                    window_name="Notepad",
                ),
                TreeElementNode(
                    bounding_box=BoundingBox(left=20, top=20, right=60, bottom=60, width=40, height=40),
                    center=Center(x=40, y=40),
                    name="Button 2",
                    control_type="Button",
                    window_name="Notepad",
                ),
            ],
            scrollable_nodes=[
                ScrollElementNode(
                    name="Scrollable 1",
                    control_type="Pane",
                    window_name="Notepad",
                    bounding_box=BoundingBox(left=60, top=60, right=100, bottom=100, width=40, height=40),
                    center=Center(x=80, y=80),
                )
            ],
        ),
    )
    return desktop


def register_tools(desktop):
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools


def test_get_coordinates_from_labels_returns_bulk_coordinates():
    desktop = make_desktop_with_tree_state()

    assert desktop.get_coordinates_from_labels([0, 1, 2]) == [(10, 10), (40, 40), (80, 80)]


def test_multiselect_uses_bulk_coordinate_resolution():
    desktop = MagicMock()
    desktop.desktop_state = object()
    desktop.get_coordinates_from_labels.return_value = [(10, 10), (40, 40)]

    tools = register_tools(desktop)
    result = asyncio.run(tools["MultiSelect"](labels=[0, 1], press_ctrl=False))

    assert result == "Multi-selected elements at:\n(10,10)\n(40,40)"
    desktop.get_coordinates_from_labels.assert_called_once_with([0, 1])
    desktop.multi_select.assert_called_once_with(False, [[10, 10], [40, 40]])


def test_multiedit_uses_bulk_coordinate_resolution():
    desktop = MagicMock()
    desktop.desktop_state = object()
    desktop.get_coordinates_from_labels.return_value = [(10, 10), (40, 40)]

    tools = register_tools(desktop)
    result = asyncio.run(tools["MultiEdit"](labels=[[0, "First"], [1, "Second"]]))

    assert result == "Multi-edited elements at: (10,10) with text 'First', (40,40) with text 'Second'"
    desktop.get_coordinates_from_labels.assert_called_once_with([0, 1])
    desktop.multi_edit.assert_called_once_with([[10, 10, "First"], [40, 40, "Second"]])
