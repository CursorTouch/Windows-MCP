"""Screenshot must not claim there are no windows when it never looked.

The Screenshot tool's fast path (use_ui_tree=False) skips window enumeration so
it cannot hang on an unresponsive app. Its answer used to print "No active
window found" and "No windows found" with Visma and Server Manager open on
screen, which reads as a fact about the desktop.
"""

from unittest.mock import MagicMock

from windows_mcp.tools import _snapshot_helpers as helpers


def _desktop() -> MagicMock:
    state = MagicMock()
    state.windows_to_string.return_value = "No windows found"
    state.active_window_to_string.return_value = "No active window found"
    state.screenshot = None
    desktop = MagicMock()
    desktop.get_state.return_value = state
    return desktop


def _capture(use_ui_tree: bool) -> dict[str, object]:
    return helpers.capture_desktop_state(
        _desktop(),
        use_vision=False,
        use_dom=False,
        use_annotation=False,
        use_ui_tree=use_ui_tree,
        width_reference_line=None,
        height_reference_line=None,
        display=None,
        region=None,
        tool_name="test",
    )


def test_screenshot_path_says_windows_were_not_listed() -> None:
    result = _capture(use_ui_tree=False)

    assert result["windows"] == helpers.WINDOWS_NOT_LISTED
    assert result["active_window"] == helpers.WINDOWS_NOT_LISTED


def test_snapshot_path_keeps_the_real_window_list() -> None:
    result = _capture(use_ui_tree=True)

    assert result["windows"] == "No windows found"
    assert result["active_window"] == "No active window found"
