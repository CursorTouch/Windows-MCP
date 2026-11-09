"""Screenshot and Snapshot draw the requested reference grid on a clean screenshot.

width_reference_line and height_reference_line overlay a grid on the image for
spatial reasoning. Desktop.get_state passed the grid only to the annotated
screenshot, so a clean one (use_annotation=False) came back without the grid,
and without an error. Screenshot leaves use_annotation off by default, so
Screenshot(width_reference_line=2, height_reference_line=2) never drew a grid.
The Snapshot description also named the parameters in the plural, and a call
with those names was rejected as an unexpected keyword argument.

Nothing is captured: get_screenshot returns a white image, and the window,
tree, cursor and virtual desktop calls are mocked.
"""

import base64
import io
import re
from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP
from PIL import Image

from windows_mcp.desktop import service
from windows_mcp.desktop.service import Desktop
from windows_mcp.tools import snapshot
from windows_mcp.tree.views import BoundingBox, TreeState

WHITE = (255, 255, 255)
ALL_WHITE = ((255, 255), (255, 255), (255, 255))
# width_reference_line=4 and height_reference_line=3 on an 800x600 image.
GRID_LINES = (4, 3)
GRID = ([200, 400, 600], [200, 400])


@pytest.fixture(autouse=True)
def mocked_desktop_queries(monkeypatch):
    monkeypatch.setattr(service.uia, "GetVirtualScreenRect", lambda: (0, 0, 800, 600))
    monkeypatch.setattr(service, "get_current_desktop", lambda: {"id": "1", "name": "Desktop 1"})
    monkeypatch.setattr(service, "get_all_desktops", lambda: [{"id": "1", "name": "Desktop 1"}])


def make_desktop(captured: Image.Image) -> Desktop:
    """A Desktop whose window, tree, cursor and screenshot calls are all mocked."""
    desktop = Desktop.__new__(Desktop)
    desktop.tree = MagicMock()
    desktop.tree.screen_box = BoundingBox(
        left=0, top=0, right=800, bottom=600, width=800, height=600
    )
    desktop.tree.get_state.return_value = TreeState(status=True)
    desktop.get_displays = MagicMock(return_value=[])
    desktop.get_controls_handles = MagicMock(return_value=[])
    desktop.get_windows = MagicMock(return_value=([], set()))
    desktop.get_active_window = MagicMock(return_value=None)
    # No cursor position, so an annotated screenshot has nothing on it but the grid.
    desktop.get_cursor_location = MagicMock(return_value=None)
    desktop.get_screenshot = MagicMock(return_value=captured)
    return desktop


def white_image() -> Image.Image:
    return Image.new("RGB", (800, 600), "white")


def grid_of(image: Image.Image) -> tuple[list[int], list[int]]:
    """The x of each vertical line and the y of each horizontal line."""
    columns = [x for x in range(image.width) if image.getpixel((x, 100)) != WHITE]
    rows = [y for y in range(image.height) if image.getpixel((100, y)) != WHITE]
    return columns, rows


@pytest.mark.parametrize("use_annotation", [False, True], ids=["clean", "annotated"])
@pytest.mark.parametrize("use_ui_tree", [False, True], ids=["screenshot", "snapshot"])
def test_screenshot_has_the_requested_grid(use_ui_tree, use_annotation):
    captured = white_image()

    state = make_desktop(captured).get_state(
        use_ui_tree=use_ui_tree,
        use_vision=True,
        use_annotation=use_annotation,
        grid_lines=GRID_LINES,
    )

    assert grid_of(state.screenshot) == GRID
    # The grid is drawn on a copy: the captured image is left as it was.
    assert captured.getextrema() == ALL_WHITE


@pytest.mark.parametrize("use_ui_tree", [False, True], ids=["screenshot", "snapshot"])
def test_clean_screenshot_without_a_grid_stays_clean(use_ui_tree):
    state = make_desktop(white_image()).get_state(
        use_ui_tree=use_ui_tree, use_vision=True, use_annotation=False
    )

    assert state.screenshot.getextrema() == ALL_WHITE


@pytest.fixture
def server(monkeypatch) -> FastMCP:
    # register() rebinds these module globals, which other tests reach through
    # windows_mcp.__main__, so put them back afterwards.
    monkeypatch.setattr(snapshot, "state_tool", snapshot.state_tool)
    monkeypatch.setattr(snapshot, "screenshot_tool", snapshot.screenshot_tool)
    desktop = make_desktop(white_image())
    mcp = FastMCP("grid-lines")
    snapshot.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("Screenshot", {}), ("Snapshot", {"use_vision": True, "use_annotation": False})],
    ids=["Screenshot", "Snapshot-clean"],
)
async def test_tool_returns_the_image_with_the_grid(server, tool, arguments):
    width, height = GRID_LINES
    async with Client(server) as client:
        result = await client.call_tool(
            tool,
            {**arguments, "width_reference_line": width, "height_reference_line": height},
            raise_on_error=False,
        )

    assert not result.is_error, result.content[0].text
    png = next(item.data for item in result.content if item.type == "image")
    image = Image.open(io.BytesIO(base64.b64decode(png))).convert("RGB")
    assert grid_of(image) == GRID


@pytest.mark.asyncio
async def test_snapshot_description_names_the_grid_parameters(server):
    async with Client(server) as client:
        tool = next(tool for tool in await client.list_tools() if tool.name == "Snapshot")

    named = set(re.findall(r"\w+_reference_\w+", tool.description))
    assert named == {"width_reference_line", "height_reference_line"}
    assert named <= set(tool.input_schema["properties"])
