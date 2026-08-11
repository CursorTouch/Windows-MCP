"""Snapshot and Screenshot tools — desktop state capture."""

import logging

from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from fastmcp import Context

from windows_mcp.tools._snapshot_helpers import (
    _as_bool,
    capture_desktop_state,
    build_snapshot_response,
)

logger = logging.getLogger(__name__)

# Populated by register(); exposed for backward-compatible test imports.
state_tool = None
screenshot_tool = None


def register(mcp, *, get_desktop, get_analytics):
    global state_tool, screenshot_tool
    @mcp.tool(
        name='Snapshot',
        description="Take a screenshot and inspect the screen. Keywords: screenshot, screen capture, see screen, observe, look, inspect, UI elements, what's on screen. Captures complete desktop state including: system language, focused/opened windows, interactive elements (buttons, text fields, links, menus with coordinates), and scrollable areas. Set use_vision=True to include screenshot with cursor highlight. Set use_annotation=False to get a clean screenshot without bounding box overlays on UI elements (default: True, draws colored rectangles around detected elements). Set use_ui_tree=False for a faster screenshot-only snapshot when you do not need interactive or scrollable element extraction. Set width_reference_lines/height_reference_lines to overlay a grid for better spatial reasoning (make sure vision is enabled to use it). Set use_dom=True for browser content to get web page elements instead of browser UI. Set display=[0] or display=[0,1] using zero-based active Windows display indices; omit it to keep the default full-desktop behavior. Always call this first to understand the current desktop state before taking actions.",
        annotations=ToolAnnotations(
            title="Snapshot",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "State-Tool")
    def _state_tool(
        use_vision: bool | str = False,
        use_dom: bool | str = False,
        use_annotation: bool | str = True,
        use_ui_tree: bool | str = True,
        width_reference_line: int | None = None,
        height_reference_line: int | None = None,
        display: list[int] | None = None,
        ctx: Context = None,
    ):
        try:
            capture_result = capture_desktop_state(
                get_desktop(),
                use_vision=_as_bool(use_vision),
                use_dom=_as_bool(use_dom),
                use_annotation=_as_bool(use_annotation),
                use_ui_tree=_as_bool(use_ui_tree),
                width_reference_line=width_reference_line,
                height_reference_line=height_reference_line,
                display=display,
                tool_name="Snapshot tool",
            )
        except Exception as e:
            logger.warning(
                "Snapshot failed with display=%s use_vision=%s use_dom=%s",
                display,
                use_vision if 'use_vision' in locals() else None,
                use_dom if 'use_dom' in locals() else None,
                exc_info=True,
            )
            return [f'Error capturing desktop state: {str(e)}. Please try again.']

        return build_snapshot_response(capture_result, include_ui_details=True)

    @mcp.tool(
        name='Screenshot',
        description="Captures a fast screenshot-first desktop snapshot with cursor position, desktop/window summaries, and an image. This path skips UI tree extraction for speed. Use Snapshot when you need interactive element ids, scrollable regions, or browser DOM extraction. Note: the returned image may be downscaled for efficiency; when it is, multiply image coordinates by the ratio of original size to displayed size to get the actual screen coordinates for mouse actions (Click, Move, etc.).",
        annotations=ToolAnnotations(
            title="Screenshot",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Screenshot-Tool")
    def _screenshot_tool(
        use_annotation: bool | str = False,
        width_reference_line: int | None = None,
        height_reference_line: int | None = None,
        display: list[int] | None = None,
        ctx: Context = None,
    ):
        try:
            capture_result = capture_desktop_state(
                get_desktop(),
                use_vision=True,
                use_dom=False,
                use_annotation=_as_bool(use_annotation),
                use_ui_tree=False,
                width_reference_line=width_reference_line,
                height_reference_line=height_reference_line,
                display=display,
                tool_name="Screenshot tool",
            )
        except Exception as e:
            logger.warning(
                "Screenshot failed with display=%s",
                display,
                exc_info=True,
            )
            return [f'Error capturing screenshot: {str(e)}. Please try again.']

        return build_snapshot_response(
            capture_result,
            include_ui_details=False,
            ui_detail_note="UI Tree: Skipped for fast screenshot-only capture. Call Snapshot when you need interactive or scrollable elements.",
        )

    state_tool = _state_tool
    screenshot_tool = _screenshot_tool
