"""Screenshot and Snapshot must say when the 1920x1080 cap lowers the configured scale.

The image is scaled by WINDOWS_MCP_SCREENSHOT_SCALE and also capped at 1920x1080, and the
smaller factor wins (Desktop.get_state). On a 3840x2160 display a full capture comes back
at 1920x1080 for any setting from 0.5 to 1.0, so a configured 0.75 gave the same image as
0.5, and nothing said so (#638). The cap stays as it is; a lowered setting is logged once.

Nothing here touches the real desktop: the displays, the virtual screen, the cursor, the
windows, the UI tree, the virtual desktop list and the capture are stand-ins.
"""

import asyncio
import io
import logging
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

import windows_mcp.__main__ as main_module
from windows_mcp.desktop.service import Desktop
from windows_mcp.tools import _snapshot_helpers
from windows_mcp.tree.views import BoundingBox, TreeState
from windows_mcp.uia import DisplayInfo, Rect

UHD = [Rect(0, 0, 3840, 2160)]
TWO_FULL_HD = [Rect(0, 0, 1920, 1080), Rect(1920, 0, 3840, 1080)]
LOWERED_TO_HALF = (
    "WINDOWS_MCP_SCREENSHOT_SCALE 0.75 lowered to 0.5 for a 3840x2160 capture: "
    "screenshots are capped at 1920x1080"
)


@pytest.fixture(autouse=True)
def _default_screenshot_scale(monkeypatch) -> None:
    monkeypatch.delenv("WINDOWS_MCP_SCREENSHOT_SCALE", raising=False)
    # Each configured scale is logged once per process; start every test with none logged.
    monkeypatch.setattr(_snapshot_helpers, "_logged_capped_scales", set(), raising=False)


def _capture(
    monkeypatch,
    monitors: list[Rect],
    *,
    tool: str = "Screenshot",
    display: list[int] | None = None,
    region: list[int] | None = None,
) -> tuple[int, int]:
    """Run the Screenshot tool, or Snapshot with use_vision=True; return the image size."""
    left = min(monitor.left for monitor in monitors)
    top = min(monitor.top for monitor in monitors)
    right = max(monitor.right for monitor in monitors)
    bottom = max(monitor.bottom for monitor in monitors)
    screen = BoundingBox(
        left=left, top=top, right=right, bottom=bottom, width=right - left, height=bottom - top
    )

    with patch.object(Desktop, "__init__", lambda self: None):
        desktop = Desktop()
    desktop.desktop_state = None
    desktop.tree = MagicMock(screen_box=screen)
    desktop.tree.get_state.return_value = TreeState()
    desktop.get_screen_box = MagicMock(return_value=screen)
    desktop.get_displays = MagicMock(
        return_value=[
            DisplayInfo(
                index=index,
                device_name=f"DISPLAY{index + 1}",
                rect=monitor,
                primary=index == 0,
            )
            for index, monitor in enumerate(monitors)
        ]
    )
    desktop.get_controls_handles = MagicMock(return_value=[])
    desktop.get_windows = MagicMock(return_value=([], set()))
    desktop.get_active_window = MagicMock(return_value=None)
    desktop.get_cursor_location = MagicMock(return_value=(10, 10))
    desktop.get_screenshot = lambda capture_rect=None: Image.new(
        "RGB",
        (capture_rect.width(), capture_rect.height())
        if capture_rect
        else (screen.width, screen.height),
    )
    monkeypatch.setattr(main_module, "desktop", desktop)
    monkeypatch.setattr(
        "windows_mcp.desktop.service.get_current_desktop", lambda: {"name": "Desktop 1"}
    )
    monkeypatch.setattr(
        "windows_mcp.desktop.service.get_all_desktops", lambda: [{"name": "Desktop 1"}]
    )

    if tool == "Snapshot":
        result = asyncio.run(
            main_module.state_tool(use_vision=True, display=display, region=region)
        )
    else:
        result = asyncio.run(main_module.screenshot_tool(display=display, region=region))
    return Image.open(io.BytesIO(result[1].data)).size


def _warnings(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize(
    ("monitors", "tool", "display", "region", "image_size", "warning"),
    [
        pytest.param(
            UHD, "Screenshot", None, None, (1920, 1080), LOWERED_TO_HALF, id="screenshot-4k"
        ),
        pytest.param(UHD, "Snapshot", None, None, (1920, 1080), LOWERED_TO_HALF, id="snapshot-4k"),
        pytest.param(UHD, "Screenshot", [0], None, (1920, 1080), LOWERED_TO_HALF, id="4k-display"),
        pytest.param(
            TWO_FULL_HD,
            "Screenshot",
            None,
            None,
            (1920, 540),
            "WINDOWS_MCP_SCREENSHOT_SCALE 0.75 lowered to 0.5 for a 3840x1080 capture: "
            "screenshots are capped at 1920x1080",
            id="two-1080p-displays",
        ),
        pytest.param(
            UHD,
            "Snapshot",
            None,
            [0, 0, 3200, 2100],
            (1645, 1080),
            "WINDOWS_MCP_SCREENSHOT_SCALE 0.75 lowered to 0.514286 for a 3200x2100 capture: "
            "screenshots are capped at 1920x1080",
            id="large-region",
        ),
    ],
)
def test_configured_scale_lowered_by_the_cap_is_logged(
    monkeypatch, caplog, monitors, tool, display, region, image_size, warning
) -> None:
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_SCALE", "0.75")

    assert _capture(monkeypatch, monitors, tool=tool, display=display, region=region) == image_size
    assert _warnings(caplog) == [warning]


def test_a_lowered_scale_is_logged_once(monkeypatch, caplog) -> None:
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_SCALE", "0.75")

    for _ in range(3):
        _capture(monkeypatch, UHD)
    _capture(monkeypatch, UHD, tool="Snapshot")
    _capture(monkeypatch, UHD, region=[0, 0, 3200, 2100])
    _capture(monkeypatch, UHD, region=[0, 0, 3200, 2100])

    # The region lowers 0.75 to another factor, but 0.75 has been reported already.
    assert _warnings(caplog) == [LOWERED_TO_HALF]


@pytest.mark.parametrize(
    ("scale", "monitors", "display", "region", "image_size"),
    [
        pytest.param("0.5", UHD, None, None, (1920, 1080), id="4k-at-0.5"),
        pytest.param("0.25", UHD, None, None, (960, 540), id="4k-at-0.25"),
        pytest.param("0.75", UHD, None, [0, 0, 1160, 140], (870, 105), id="small-region"),
        pytest.param("0.75", UHD, None, [0, 0, 2280, 840], (1710, 630), id="wide-region"),
        pytest.param("0.75", TWO_FULL_HD, [1], None, (1440, 810), id="1080p-display"),
    ],
)
def test_configured_scale_applied_as_set_is_not_logged(
    monkeypatch, caplog, scale, monitors, display, region, image_size
) -> None:
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_SCALE", scale)

    assert _capture(monkeypatch, monitors, display=display, region=region) == image_size
    assert _warnings(caplog) == []


@pytest.mark.parametrize(
    ("monitors", "image_size"),
    [
        pytest.param(UHD, (1920, 1080), id="4k"),
        pytest.param(TWO_FULL_HD, (1920, 540), id="two-1080p-displays"),
    ],
)
def test_cap_with_the_scale_unset_is_not_logged(monkeypatch, caplog, monitors, image_size) -> None:
    # Without a configured scale the cap alone sizes the image, as documented; logging it
    # would warn every 4K and multi-monitor user on their first capture.
    assert _capture(monkeypatch, monitors) == image_size
    assert _warnings(caplog) == []


def test_full_scale_set_explicitly_and_lowered_by_the_cap_is_logged(monkeypatch, caplog) -> None:
    # 1.0 set on purpose asks for a full-size image, which the cap does not allow on 4K.
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_SCALE", "1.0")

    assert _capture(monkeypatch, UHD) == (1920, 1080)
    assert _warnings(caplog) == [
        "WINDOWS_MCP_SCREENSHOT_SCALE 1.0 lowered to 0.5 for a 3840x2160 capture: "
        "screenshots are capped at 1920x1080"
    ]
