"""Screenshot and Snapshot must say where the returned image starts on the screen.

Image pixel (0, 0) is the top-left corner of what was captured: the region or the
displays asked for, else the virtual screen, which starts left of or above (0, 0)
when a monitor sits left of or above the primary one. The response told the agent to
turn image pixels into screen coordinates by scaling alone (#453), so a point worked
out from the image missed by that corner's position, often onto another monitor.

Nothing here touches the real desktop: the displays, the virtual screen, the cursor,
the virtual desktop list and the capture are stand-ins. The capture is cut out of a
synthetic virtual desktop with a red button on it, the way the pillow and mss
backends cut a region out of the virtual screen.
"""

import io
import re
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageDraw

from windows_mcp.desktop.service import Desktop
from windows_mcp.tools._snapshot_helpers import build_snapshot_response, capture_desktop_state
from windows_mcp.tree.views import BoundingBox
from windows_mcp.uia import DisplayInfo, Rect

EXAMPLE = re.compile(r"image pixel \(200, 150\) → screen coordinate \((-?\d+), (-?\d+)\)")
SCALE = re.compile(r"Screenshot Coordinate Scale: ([0-9.]+)")


@pytest.fixture(autouse=True)
def _default_screenshot_scale(monkeypatch) -> None:
    monkeypatch.delenv("WINDOWS_MCP_SCREENSHOT_SCALE", raising=False)


def _screenshot(
    monitors: list[Rect],
    button: tuple[int, int],
    *,
    display: list[int] | None = None,
    region: list[int] | None = None,
) -> tuple[str, Image.Image]:
    """Run the Screenshot tool's capture; return the response text and the image."""
    left = min(monitor.left for monitor in monitors)
    top = min(monitor.top for monitor in monitors)
    right = max(monitor.right for monitor in monitors)
    bottom = max(monitor.bottom for monitor in monitors)

    virtual_screen = Image.new("RGB", (right - left, bottom - top), (40, 40, 40))
    x, y = button[0] - left, button[1] - top
    ImageDraw.Draw(virtual_screen).rectangle([x - 5, y - 5, x + 5, y + 5], fill=(255, 0, 0))

    def get_screenshot(capture_rect: Rect | None = None) -> Image.Image:
        if capture_rect is None:
            return virtual_screen.copy()
        return virtual_screen.crop(
            (
                capture_rect.left - left,
                capture_rect.top - top,
                capture_rect.right - left,
                capture_rect.bottom - top,
            )
        )

    with patch.object(Desktop, "__init__", lambda self: None):
        desktop = Desktop()
    desktop.desktop_state = None
    desktop.tree = MagicMock()
    desktop.tree.screen_box = BoundingBox(
        left=left, top=top, right=right, bottom=bottom, width=right - left, height=bottom - top
    )
    desktop.get_displays = lambda: [
        DisplayInfo(
            index=index,
            device_name=f"DISPLAY{index + 1}",
            rect=monitor,
            primary=(monitor.left, monitor.top) == (0, 0),
        )
        for index, monitor in enumerate(monitors)
    ]
    desktop.get_cursor_location = MagicMock(return_value=(0, 0))
    desktop.get_screenshot = get_screenshot

    with (
        patch(
            "windows_mcp.desktop.service.uia.GetVirtualScreenRect",
            return_value=(left, top, right - left, bottom - top),
        ),
        patch(
            "windows_mcp.desktop.service.get_current_desktop",
            return_value={"name": "Desktop 1"},
        ),
        patch(
            "windows_mcp.desktop.service.get_all_desktops",
            return_value=[{"name": "Desktop 1"}],
        ),
    ):
        # The arguments the Screenshot tool passes (tools/snapshot.py).
        capture = capture_desktop_state(
            desktop,
            use_vision=True,
            use_dom=False,
            use_annotation=False,
            use_ui_tree=False,
            width_reference_line=None,
            height_reference_line=None,
            display=display,
            region=region,
            tool_name="Screenshot tool",
        )
    text, image = build_snapshot_response(capture, include_ui_details=False)
    return text, Image.open(io.BytesIO(image.data)).convert("RGB")


def _button_pixel(image: Image.Image) -> tuple[float, float]:
    """Centre of the red button in the returned image."""
    red = [
        (x, y)
        for y in range(image.height)
        for x in range(image.width)
        if image.getpixel((x, y))[0] > 200 and image.getpixel((x, y))[1] < 80
    ]
    assert red, "the button is not in the returned image"
    return sum(x for x, _ in red) / len(red), sum(y for _, y in red) / len(red)


def _to_screen(text: str, pixel: tuple[float, float]) -> tuple[float, float]:
    """Map an image pixel to the screen the way the response's worked example does."""
    example = EXAMPLE.search(text)
    assert example, "the response says nothing about turning image pixels into screen coordinates"
    scale = SCALE.search(text)
    factor = float(scale.group(1)) if scale else 1.0
    return (
        int(example.group(1)) + (pixel[0] - 200) * factor,
        int(example.group(2)) + (pixel[1] - 150) * factor,
    )


@pytest.mark.parametrize(
    ("monitors", "button", "display", "region", "screenshot_scale"),
    [
        pytest.param(
            [Rect(0, 0, 1920, 1080), Rect(-1920, 0, 0, 1080)],
            (-1500, 300),
            None,
            None,
            None,
            id="full-capture-monitor-left-of-primary",
        ),
        pytest.param(
            [Rect(0, 0, 1920, 1080), Rect(0, -1080, 1920, 0)],
            (700, -400),
            None,
            None,
            None,
            id="full-capture-monitor-above-primary",
        ),
        pytest.param(
            [Rect(0, 0, 1920, 1080), Rect(1920, 0, 4480, 1440)],
            (2500, 600),
            [1],
            None,
            None,
            id="display-right-of-primary",
        ),
        pytest.param(
            [Rect(0, 0, 3840, 2160)],
            (2400, 1300),
            None,
            [2000, 1000, 3000, 1600],
            "0.5",
            id="region-downscaled",
        ),
        pytest.param(
            [Rect(0, 0, 1920, 1080)],
            (300, 250),
            None,
            [100, 100, 900, 750],
            None,
            id="region-full-size",
        ),
    ],
)
def test_image_pixel_maps_to_its_screen_coordinate(
    monkeypatch, monitors, button, display, region, screenshot_scale
) -> None:
    if screenshot_scale is not None:
        monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_SCALE", screenshot_scale)

    text, image = _screenshot(monitors, button, display=display, region=region)
    x, y = _to_screen(text, _button_pixel(image))

    assert abs(x - button[0]) <= 2 and abs(y - button[1]) <= 2, (
        f"the response maps the button's image pixel to ({x:.0f}, {y:.0f}), "
        f"but the button is at {button}"
    )


def test_downscaled_capture_at_screen_origin_keeps_its_note() -> None:
    text, image = _screenshot([Rect(0, 0, 3840, 2160)], (2400, 1300))

    assert image.size == (1920, 1080)
    assert (
        "Screenshot Coordinate Scale: 2.0 — image pixels are downscaled; multiply every image "
        "pixel coordinate by 2.0 before passing to Click, Move, Scroll, or any loc= argument "
        "(e.g. image pixel (200, 150) → screen coordinate (400, 300))"
    ) in text


def test_full_size_capture_at_screen_origin_needs_no_conversion() -> None:
    text, image = _screenshot([Rect(0, 0, 1920, 1080)], (300, 250))

    assert image.size == (1920, 1080)
    assert "Screenshot Size: (1920,1080)" in text
    assert "image pixel (200, 150)" not in text
