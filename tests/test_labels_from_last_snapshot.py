"""Click, Type, Scroll and Move resolve a label against the elements of the last Snapshot.

The server instructions tell agents to act "by loc=[x, y] or label=<element id> from the last
Snapshot". A label is an element's place in the element lists of the state the Desktop keeps
(Desktop.desktop_state), and every capture replaced that state, not only Snapshot. After a
Screenshot, which collects no elements, every label of the last Snapshot was out of range.
After WaitFor, or Scrape with use_dom=True, the labels indexed a UI tree that the caller never
saw, so a label could send the click to another element.

Nothing here touches the real desktop: the displays, the windows, the cursor, the UI tree, the
virtual desktops and the capture are stand-ins, and Desktop.click is a mock.
"""

import asyncio
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from fastmcp.utilities.types import Image as ToolImage
from PIL import Image

from windows_mcp.desktop.service import Desktop
from windows_mcp.tools import input as input_tools
from windows_mcp.tools import scrape as scrape_tools
from windows_mcp.tools import snapshot as snapshot_tools
from windows_mcp.tree.views import (
    BoundingBox,
    ScrollElementNode,
    SemanticNode,
    TextElementNode,
    TreeElementNode,
    TreeState,
)
from windows_mcp.uia import DisplayInfo, Rect

SERVICE = "windows_mcp.desktop.service"
SCREEN = BoundingBox(left=0, top=0, right=1920, bottom=1080, width=1920, height=1080)


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


def make_box(left: int, top: int, right: int, bottom: int) -> BoundingBox:
    return BoundingBox(
        left=left, top=top, right=right, bottom=bottom, width=right - left, height=bottom - top
    )


def button(name: str, window_name: str, box: BoundingBox) -> TreeElementNode:
    return TreeElementNode(
        name=name,
        control_type="Button",
        window_name=window_name,
        bounding_box=box,
        center=box.get_center(),
    )


def ui_tree(*windows: tuple[str, list[TreeElementNode]], **dom: object) -> TreeState:
    """A capture listing each window's buttons in order, numbered as Snapshot shows them."""
    root = SemanticNode(control_type="Desktop", element_type="desktop")
    elements = []
    for window_name, buttons in windows:
        window = SemanticNode(
            control_type="Window", element_type="window", name=window_name, window_name=window_name
        )
        for element in buttons:
            window.add_child(
                SemanticNode(
                    control_type="Button",
                    element_type="interactive",
                    name=element.name,
                    window_name=window_name,
                    center=element.center,
                    bounding_box=element.bounding_box,
                    element=element,
                )
            )
        root.add_child(window)
        elements.extend(buttons)
    return TreeState(interactive_nodes=elements, semantic_tree_root=root, **dom)


NOTEPAD = "Untitled - Notepad"
SAVE = button("Save", NOTEPAD, make_box(420, 330, 480, 370))  # center (450,350)
CANCEL = button("Cancel", NOTEPAD, make_box(500, 330, 560, 370))  # center (530,350)
NOTEPAD_TREE = ui_tree((NOTEPAD, [SAVE, CANCEL]))
# A notification has opened in front of Notepad: its button is walked first.
DISMISS = button("Dismiss", "File saved", make_box(1700, 930, 1780, 970))
NOTIFICATION_TREE = ui_tree(("File saved", [DISMISS]), (NOTEPAD, [SAVE, CANCEL]))

EDGE = "Example Domain - Microsoft Edge"
BACK = button("Back", EDGE, make_box(0, 40, 40, 80))
REFRESH = button("Refresh", EDGE, make_box(80, 40, 120, 80))  # center (100,60)
BROWSER_TREE = ui_tree((EDGE, [BACK, REFRESH]))
# Scrape(use_dom=True) walks the page instead of the browser's own controls.
PAGE = make_box(0, 120, 1920, 1040)
PAGE_TREE = ui_tree(
    (
        EDGE,
        [
            button("More information", EDGE, make_box(380, 300, 520, 330)),
            button("Search", EDGE, make_box(380, 400, 520, 430)),
        ],
    ),
    dom_node=ScrollElementNode(
        name="Example Domain",
        control_type="DocumentControl",
        window_name=EDGE,
        bounding_box=PAGE,
        center=PAGE.get_center(),
        metadata={"vertical_scrollable": False},
    ),
    dom_informative_nodes=[TextElementNode(text="Example Domain")],
)


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> Desktop:
    """A Desktop over stand-ins; desktop.tree.get_state gives the UI tree of each capture."""
    instance = Desktop.__new__(Desktop)
    instance.desktop_state = None
    instance.tree = MagicMock(screen_box=SCREEN)
    instance.get_displays = MagicMock(
        return_value=[
            DisplayInfo(index=0, device_name="DISPLAY1", rect=Rect(0, 0, 1920, 1080), primary=True)
        ]
    )
    instance.get_controls_handles = MagicMock(return_value=[])
    instance.get_windows = MagicMock(return_value=([], set()))
    instance.get_active_window = MagicMock(return_value=None)
    instance.get_cursor_location = MagicMock(return_value=(960, 540))
    instance.get_screenshot = MagicMock(
        side_effect=lambda capture_rect=None: Image.new("RGB", (SCREEN.width, SCREEN.height))
    )
    instance.click = MagicMock(return_value="")
    monkeypatch.setattr(f"{SERVICE}.get_current_desktop", lambda: {"name": "Desktop 1"})
    monkeypatch.setattr(f"{SERVICE}.get_all_desktops", lambda: [{"name": "Desktop 1"}])
    return instance


@pytest.fixture
def tools(desktop: Desktop, monkeypatch: pytest.MonkeyPatch) -> dict[str, Callable]:
    # register() rebinds these module globals, which other tests reach through
    # windows_mcp.__main__, so put them back afterwards.
    monkeypatch.setattr(snapshot_tools, "state_tool", snapshot_tools.state_tool)
    monkeypatch.setattr(snapshot_tools, "screenshot_tool", snapshot_tools.screenshot_tool)
    mcp = FakeMCP()
    for module in (snapshot_tools, input_tools, scrape_tools):
        module.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        pytest.param("Screenshot", {}, id="screenshot"),
        pytest.param("Snapshot", {"use_ui_tree": False}, id="snapshot-without-ui-tree"),
    ],
)
def test_capture_without_ui_tree_keeps_the_labels(
    desktop: Desktop, tools: dict[str, Callable], tool: str, arguments: dict[str, object]
) -> None:
    desktop.tree.get_state.side_effect = [NOTEPAD_TREE]
    snapshot = asyncio.run(tools["Snapshot"]())
    assert '[#1] (530,350) button "Cancel"' in snapshot[0]

    asyncio.run(tools[tool](**arguments))

    assert asyncio.run(tools["Click"](label=1)) == "Single left clicked at (530,350)."
    desktop.click.assert_called_once_with(loc=[530, 350], button="left", clicks=1)


def test_screenshot_still_returns_its_own_capture(
    desktop: Desktop, tools: dict[str, Callable]
) -> None:
    desktop.tree.get_state.side_effect = [NOTEPAD_TREE]
    asyncio.run(tools["Snapshot"]())

    screenshot = asyncio.run(tools["Screenshot"]())

    assert isinstance(screenshot[-1], ToolImage)


def test_wait_for_keeps_the_labels_of_the_last_snapshot(
    desktop: Desktop, tools: dict[str, Callable]
) -> None:
    desktop.tree.get_state.side_effect = [NOTEPAD_TREE, NOTIFICATION_TREE]
    snapshot = asyncio.run(tools["Snapshot"]())
    assert '[#1] (530,350) button "Cancel"' in snapshot[0]

    waited = asyncio.run(
        tools["WaitFor"](condition="element_exists", text="Dismiss", timeout=1, interval=0.01)
    )
    assert "element matched 'Dismiss'" in waited

    # Label 1 is Cancel, as the Snapshot showed, not Save, the second element WaitFor saw.
    assert asyncio.run(tools["Click"](label=1)) == "Single left clicked at (530,350)."


def test_scrape_with_dom_keeps_the_labels_of_the_last_snapshot(
    desktop: Desktop, tools: dict[str, Callable]
) -> None:
    desktop.tree.get_state.side_effect = [BROWSER_TREE, PAGE_TREE]
    snapshot = asyncio.run(tools["Snapshot"]())
    assert '[#1] (100,60) button "Refresh"' in snapshot[0]

    page = asyncio.run(tools["Scrape"](url="https://example.com", use_dom=True, use_sampling=False))
    assert "Example Domain" in page

    # Label 1 is the browser's Refresh button, not the Search button on the page Scrape walked.
    assert asyncio.run(tools["Click"](label=1)) == "Single left clicked at (100,60)."


def test_each_snapshot_sets_the_labels(desktop: Desktop, tools: dict[str, Callable]) -> None:
    desktop.tree.get_state.side_effect = [NOTEPAD_TREE, NOTIFICATION_TREE]
    asyncio.run(tools["Snapshot"]())
    snapshot = asyncio.run(tools["Snapshot"]())
    assert '[#1] (450,350) button "Save"' in snapshot[0]

    assert asyncio.run(tools["Click"](label=1)) == "Single left clicked at (450,350)."


def test_label_before_any_snapshot_asks_for_one(
    desktop: Desktop, tools: dict[str, Callable]
) -> None:
    asyncio.run(tools["Screenshot"]())

    with pytest.raises(ValueError, match="Please call Snapshot first"):
        asyncio.run(tools["Click"](label=0))
    desktop.click.assert_not_called()
