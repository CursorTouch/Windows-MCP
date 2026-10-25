"""Cursor metadata must reflect movement during desktop/tree collection."""

from unittest.mock import MagicMock

import pytest
from PIL import Image

from windows_mcp.desktop import service
from windows_mcp.desktop.service import Desktop
from windows_mcp.tree.views import BoundingBox, TreeState
from windows_mcp.uia import Rect


@pytest.mark.parametrize(
    ("use_ui_tree", "use_vision", "use_annotation"),
    [(False, True, False), (False, True, True), (True, True, True), (True, False, False)],
    ids=["screenshot", "annotated-screenshot", "snapshot", "text-snapshot"],
)
@pytest.mark.parametrize(
    ("region", "before", "after", "expected"),
    [
        (None, (25, 30), (150, 160), (150, 160)),
        ([100, 100, 300, 300], (25, 30), (150, 160), (150, 160)),
        ([100, 100, 300, 300], (150, 160), (25, 30), None),
    ],
    ids=["full-desktop", "entered-region", "left-region"],
)
def test_cursor_is_sampled_after_desktop_and_tree_collection(
    monkeypatch, use_ui_tree, use_vision, use_annotation, region, before, after, expected
):
    desktop = Desktop.__new__(Desktop)
    desktop.tree = MagicMock()
    desktop.tree.screen_box = BoundingBox(
        left=0, top=0, right=800, bottom=600, width=800, height=600
    )
    desktop.get_displays = MagicMock(return_value=[])
    desktop.get_controls_handles = MagicMock(return_value=set())
    desktop.get_windows = MagicMock(return_value=([], set()))
    desktop.get_active_window = MagicMock(return_value=None)
    monkeypatch.setattr(service.uia, "GetVirtualScreenRect", lambda: (0, 0, 800, 600))

    position = before
    desktop.get_cursor_location = MagicMock(side_effect=lambda: position)

    def collect_desktops():
        nonlocal position
        # Screenshot skips the UI tree, but still queries virtual desktops.
        if not use_ui_tree:
            position = after
        return [{"name": "Desktop 1"}]

    def collect_tree(*args, **kwargs):
        nonlocal position
        position = after
        return TreeState(status=True)

    monkeypatch.setattr(service, "get_current_desktop", lambda: {"name": "Desktop 1"})
    monkeypatch.setattr(service, "get_all_desktops", collect_desktops)
    desktop.tree.get_state.side_effect = collect_tree
    screenshot = Image.new("RGB", (200, 200))
    desktop.get_screenshot = MagicMock(return_value=screenshot)
    desktop.get_annotated_screenshot = MagicMock(return_value=screenshot)

    state = desktop.get_state(
        use_ui_tree=use_ui_tree,
        use_vision=use_vision,
        use_annotation=use_annotation,
        region=region,
    )

    assert state.cursor_position == expected
    desktop.get_cursor_location.assert_called_once_with()
    if use_vision and use_annotation:
        desktop.get_annotated_screenshot.assert_called_once_with(
            nodes=[],
            cursor_pos=expected,
            grid_lines=None,
            capture_rect=Rect(*region) if region else None,
        )
