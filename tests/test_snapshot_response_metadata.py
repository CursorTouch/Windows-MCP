from unittest.mock import MagicMock

from PIL import Image

from windows_mcp.desktop.views import DesktopState, Display, Size
from windows_mcp.tools._snapshot_helpers import (
    WINDOW_METADATA_NOT_QUERIED,
    build_snapshot_response,
    capture_desktop_state,
)
from windows_mcp.tree.views import BoundingBox, TreeState


def make_box(left: int, top: int, right: int, bottom: int) -> BoundingBox:
    return BoundingBox(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        width=right - left,
        height=bottom - top,
    )


def make_state(
    *,
    screenshot=None,
    screenshot_original_size: Size | None = None,
    screenshot_region: BoundingBox | None = None,
    available_displays: list[Display] | None = None,
) -> DesktopState:
    return DesktopState(
        active_desktop={"name": "Desktop 1"},
        all_desktops=[{"name": "Desktop 1"}],
        active_window=None,
        windows=[],
        screenshot=screenshot,
        cursor_position=(25, 30),
        screenshot_original_size=screenshot_original_size,
        screenshot_region=screenshot_region,
        available_displays=available_displays,
        tree_state=TreeState(),
    )


def make_capture_result(state: DesktopState) -> dict[str, object]:
    return {
        "desktop_state": state,
        "interactive_elements": "",
        "scrollable_elements": "",
        "semantic_tree": "",
        "windows": "No windows found",
        "active_window": "No active window found",
        "active_desktop": state.active_desktop_to_string(),
        "all_desktops": state.desktops_to_string(),
        "screenshot_bytes": None,
    }


def test_fast_capture_marks_window_metadata_as_not_queried():
    desktop = MagicMock()
    desktop.get_state.return_value = make_state()

    result = capture_desktop_state(
        desktop,
        use_vision=False,
        use_dom=False,
        use_annotation=False,
        use_ui_tree=False,
        width_reference_line=None,
        height_reference_line=None,
        display=None,
        region=None,
        tool_name="Screenshot tool",
    )

    assert result["active_window"] == WINDOW_METADATA_NOT_QUERIED
    assert result["windows"] == WINDOW_METADATA_NOT_QUERIED


def test_ui_tree_capture_keeps_real_empty_window_metadata():
    desktop = MagicMock()
    desktop.get_state.return_value = make_state()

    result = capture_desktop_state(
        desktop,
        use_vision=False,
        use_dom=False,
        use_annotation=False,
        use_ui_tree=True,
        width_reference_line=None,
        height_reference_line=None,
        display=None,
        region=None,
        tool_name="Snapshot tool",
    )

    assert result["active_window"] == "No active window found"
    assert result["windows"] == "No windows found"


def test_cropped_downscaled_screenshot_reports_exact_per_axis_transform():
    state = make_state(
        screenshot=Image.new("RGB", (300, 200), "white"),
        screenshot_original_size=Size(width=601, height=400),
        screenshot_region=make_box(100, 100, 701, 500),
    )

    response = build_snapshot_response(
        make_capture_result(state),
        include_ui_details=False,
    )[0]

    assert "Screenshot Original Size: (601,400)" in response
    assert "Screenshot Returned Size: (300,200)" in response
    assert "Screenshot Region: (100,100,701,500)" in response
    assert (
        "Screenshot Coordinate Mapping: screen_x = 100 + image_x * 601 / 300; "
        "screen_y = 100 + image_y * 400 / 200"
    ) in response
    assert "UI-tree coordinates are already screen coordinates and must not be transformed." in response
    assert "Screenshot Coordinate Scale:" not in response


def test_full_virtual_desktop_mapping_uses_negative_display_origin():
    state = make_state(
        screenshot=Image.new("RGB", (3840, 1080), "white"),
        screenshot_original_size=Size(width=3840, height=1080),
        available_displays=[
            Display(
                index=1,
                device_name="\\\\.\\DISPLAY2",
                bounding_box=make_box(-1920, 0, 0, 1080),
                primary=False,
            ),
            Display(
                index=0,
                device_name="\\\\.\\DISPLAY1",
                bounding_box=make_box(0, 0, 1920, 1080),
                primary=True,
            ),
        ],
    )

    response = build_snapshot_response(
        make_capture_result(state),
        include_ui_details=False,
    )[0]

    assert "Screenshot Size: (3840,1080)" in response
    assert (
        "Screenshot Coordinate Mapping: screen_x = -1920 + image_x * 3840 / 3840; "
        "screen_y = 0 + image_y * 1080 / 1080"
    ) in response
