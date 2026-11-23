"""Snapshot / Screenshot shared helpers.

These functions were originally defined in ``__main__.py`` and are now shared
by the Snapshot and Screenshot tool modules.
"""

import io
import json
import logging
import os
import time

from fastmcp.utilities.types import Image
from textwrap import dedent
from windows_mcp.desktop.service import Desktop, Size
from windows_mcp.desktop.utils import remove_private_use_chars, repair_surrogates
from windows_mcp.desktop.views import DesktopState


logger = logging.getLogger(__name__)

DEFAULT_MAX_IMAGE_WIDTH, DEFAULT_MAX_IMAGE_HEIGHT = 1920, 1080
# Kept for code that imports the defaults under their old names.
MAX_IMAGE_WIDTH, MAX_IMAGE_HEIGHT = DEFAULT_MAX_IMAGE_WIDTH, DEFAULT_MAX_IMAGE_HEIGHT

# Configured scales already logged as lowered by the image cap. Every full capture of a
# 4K screen lowers the scale the same way, so each configured value is logged once per
# process instead of on every Screenshot and Snapshot.
_logged_capped_scales: set[float] = set()


def _screenshot_scale() -> float:
    value = os.getenv("WINDOWS_MCP_SCREENSHOT_SCALE", "1.0")
    try:
        scale = float(value)
    except ValueError:
        logger.warning("Invalid WINDOWS_MCP_SCREENSHOT_SCALE value %r, using 1.0", value)
        scale = 1.0
    if not (0.1 <= scale <= 1.0):
        logger.warning("WINDOWS_MCP_SCREENSHOT_SCALE %r out of range [0.1, 1.0], clamping", scale)
        scale = max(0.1, min(1.0, scale))
    return scale


def _max_image_size() -> Size | None:
    """Read WINDOWS_MCP_MAX_IMAGE_SIZE: ``WIDTHxHEIGHT``, or ``none`` for no cap.

    Unset or empty keeps the default 1920x1080 cap. An invalid value is logged and
    the default is used.
    """
    default = Size(width=DEFAULT_MAX_IMAGE_WIDTH, height=DEFAULT_MAX_IMAGE_HEIGHT)
    value = os.getenv("WINDOWS_MCP_MAX_IMAGE_SIZE", "").strip().lower()
    if not value:
        return default
    if value in {"none", "off", "0"}:
        return None
    try:
        width, height = (int(part) for part in value.split("x"))
        if width <= 0 or height <= 0:
            raise ValueError(value)
    except ValueError:
        logger.warning(
            "Invalid WINDOWS_MCP_MAX_IMAGE_SIZE value %r, using %dx%d",
            value,
            default.width,
            default.height,
        )
        return default
    return Size(width=width, height=height)


def _log_capped_scale(
    scale: float, desktop_state: DesktopState, max_image_size: Size | None = None
) -> None:
    """Warn once when the image cap lowered a WINDOWS_MCP_SCREENSHOT_SCALE that was set.

    Desktop.get_state() applies the smaller of the configured scale and the one that fits
    the capture in the image cap (WINDOWS_MCP_MAX_IMAGE_SIZE, 1920x1080 by default). With
    the variable unset no scale was asked for and the cap alone sizes the image, so that
    case is not reported. Without a cap nothing can lower the scale.
    """
    if max_image_size is None:
        return
    if not os.getenv("WINDOWS_MCP_SCREENSHOT_SCALE", "").strip():
        return
    applied_scale = desktop_state.screenshot_scale
    if applied_scale is None or applied_scale >= scale:
        return
    if scale in _logged_capped_scales:
        return
    _logged_capped_scales.add(scale)
    size = desktop_state.screenshot_original_size
    logger.warning(
        "WINDOWS_MCP_SCREENSHOT_SCALE %s lowered to %s for a %dx%d capture: "
        "screenshots are capped at %dx%d",
        scale,
        round(applied_scale, 6),
        size.width,
        size.height,
        max_image_size.width,
        max_image_size.height,
    )


def _snapshot_profile_enabled() -> bool:
    value = os.getenv("WINDOWS_MCP_PROFILE_SNAPSHOT", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_region(value: list | str | None) -> list | None:
    if value is None or isinstance(value, list):
        return value
    return json.loads(value)


WINDOWS_NOT_LISTED = "Not listed: Screenshot skips window enumeration. Call Snapshot for windows."


def _image_origin(desktop_state: DesktopState) -> tuple[int, int]:
    """Return the screen coordinate of the screenshot's top-left pixel.

    A region or display capture starts at the corner of its rectangle. A full capture
    covers the virtual screen, which starts left of or above (0, 0) when a monitor sits
    left of or above the primary one.
    """
    region = desktop_state.screenshot_region
    if region is not None:
        return region.left, region.top
    displays = desktop_state.available_displays
    if not displays:
        return 0, 0
    return (
        min(display.bounding_box.left for display in displays),
        min(display.bounding_box.top for display in displays),
    )


def capture_desktop_state(
    desktop: Desktop,
    *,
    use_vision: bool,
    use_dom: bool,
    use_annotation: bool,
    use_ui_tree: bool,
    width_reference_line: int | None,
    height_reference_line: int | None,
    display: list[int] | None,
    region: list[int] | None,
    tool_name: str,
):
    profile_enabled = _snapshot_profile_enabled()
    profile_started_at = time.perf_counter()
    stage_started_at = profile_started_at
    desktop_state_ms = 0.0
    metadata_render_ms = 0.0
    screenshot_encode_ms = 0.0

    if use_dom and not use_ui_tree:
        raise ValueError("use_dom=True requires use_ui_tree=True")

    display_indices = Desktop.parse_display_selection(display)

    grid_lines = None
    if width_reference_line and height_reference_line:
        grid_lines = (int(width_reference_line), int(height_reference_line))

    scale = _screenshot_scale()
    max_image_size = _max_image_size()
    desktop_state = desktop.get_state(
        use_vision=use_vision,
        use_dom=use_dom,
        use_annotation=use_annotation,
        use_ui_tree=use_ui_tree,
        as_bytes=False,
        scale=scale,
        grid_lines=grid_lines,
        display_indices=display_indices,
        region=region,
        max_image_size=max_image_size,
    )
    _log_capped_scale(scale, desktop_state, max_image_size)
    if profile_enabled:
        desktop_state_ms = (time.perf_counter() - stage_started_at) * 1000
        stage_started_at = time.perf_counter()

    interactive_elements = desktop_state.tree_state.interactive_elements_to_string()
    scrollable_elements = desktop_state.tree_state.scrollable_elements_to_string()
    semantic_tree = desktop_state.tree_state.semantic_tree_to_string()
    if use_ui_tree:
        windows = desktop_state.windows_to_string()
        active_window = desktop_state.active_window_to_string()
    else:
        # The fast path never enumerates windows (see Desktop.get_state), so an empty
        # list here means "not looked at", not "there are none".
        windows = active_window = WINDOWS_NOT_LISTED
    active_desktop = desktop_state.active_desktop_to_string()
    all_desktops = desktop_state.desktops_to_string()
    if profile_enabled:
        metadata_render_ms = (time.perf_counter() - stage_started_at) * 1000
        stage_started_at = time.perf_counter()

    screenshot_bytes = None
    if use_vision and desktop_state.screenshot is not None:
        buffered = io.BytesIO()
        desktop_state.screenshot.save(buffered, format="PNG")
        screenshot_bytes = buffered.getvalue()
        buffered.close()
    # The Desktop keeps this state until the next capture. Everything after this
    # point (response, save_path) uses the PNG bytes, so drop the full-resolution
    # image instead of holding a copy of the screen in memory between calls.
    desktop_state.screenshot = None
    if profile_enabled:
        screenshot_encode_ms = (time.perf_counter() - stage_started_at) * 1000
        logger.info(
            "%s profile: desktop_state_ms=%.1f metadata_render_ms=%.1f png_encode_ms=%.1f total_ms=%.1f use_vision=%s use_dom=%s use_ui_tree=%s use_annotation=%s display=%s",
            tool_name,
            desktop_state_ms,
            metadata_render_ms,
            screenshot_encode_ms,
            (time.perf_counter() - profile_started_at) * 1000,
            use_vision,
            use_dom,
            use_ui_tree,
            use_annotation,
            display,
        )

    return {
        "desktop_state": desktop_state,
        "interactive_elements": interactive_elements,
        "scrollable_elements": scrollable_elements,
        "semantic_tree": semantic_tree,
        "windows": windows,
        "active_window": active_window,
        "active_desktop": active_desktop,
        "all_desktops": all_desktops,
        "screenshot_bytes": screenshot_bytes,
        "use_ui_tree": use_ui_tree,
    }


def build_snapshot_response(
    capture_result: dict[str, object],
    *,
    include_ui_details: bool,
    ui_detail_note: str | None = None,
    saved_path: str | None = None,
):
    desktop_state = capture_result["desktop_state"]
    interactive_elements = capture_result["interactive_elements"]
    scrollable_elements = capture_result["scrollable_elements"]
    semantic_tree = capture_result["semantic_tree"]
    windows = capture_result["windows"]
    active_window = capture_result["active_window"]
    active_desktop = capture_result["active_desktop"]
    all_desktops = capture_result["all_desktops"]
    screenshot_bytes = capture_result["screenshot_bytes"]

    # Some applications (e.g. VS Code) embed Unicode Private Use Area characters in the
    # Automation Element Name property of certain UI elements (e.g. navigation bar items in VS Code).
    # These characters can cause display issues, so we strip them out before rendering.
    interactive_elements = remove_private_use_chars(interactive_elements)
    scrollable_elements = remove_private_use_chars(scrollable_elements)
    semantic_tree = remove_private_use_chars(semantic_tree)

    # Emoji reach us from UIA as raw UTF-16 surrogate pairs. Left alone they make
    # the strict UTF-8 JSON encoder reject the entire response, so every string
    # heading into it is repaired -- window titles included, since an emoji in a
    # window title is enough to take the whole snapshot down.
    interactive_elements = repair_surrogates(interactive_elements)
    scrollable_elements = repair_surrogates(scrollable_elements)
    semantic_tree = repair_surrogates(semantic_tree)
    windows = repair_surrogates(windows)
    active_window = repair_surrogates(active_window)
    active_desktop = repair_surrogates(active_desktop)
    all_desktops = repair_surrogates(all_desktops)

    def display_to_string(display):
        primary = " primary" if display.primary else ""
        return (
            f"{display.index}:{display.device_name} "
            f"{display.bounding_box.xyxy_to_string()}{primary}"
        )

    metadata_text = f"Cursor Position: {desktop_state.cursor_position}\n"
    if desktop_state.screenshot_original_size:
        orig = desktop_state.screenshot_original_size
        scale = desktop_state.screenshot_scale or 1.0
        origin_x, origin_y = _image_origin(desktop_state)
        origin = f"({origin_x},{origin_y})"
        if scale < 1.0:
            coord_scale = round(1.0 / scale, 6)
            add_origin = ""
            if (origin_x, origin_y) != (0, 0):
                add_origin = f", then add {origin}, the screen coordinate of image pixel (0, 0),"
            metadata_text += (
                f"Screenshot Original Size: {orig.to_string()}\n"
                f"Screenshot Coordinate Scale: {coord_scale} "
                f"— image pixels are downscaled; multiply every image pixel coordinate by "
                f"{coord_scale}{add_origin} before passing to Click, Move, Scroll, or any loc= argument "
                f"(e.g. image pixel (200, 150) → screen coordinate ({origin_x + round(200 * coord_scale)}, {origin_y + round(150 * coord_scale)}))\n"
            )
        else:
            metadata_text += f"Screenshot Size: {orig.to_string()}\n"
            if (origin_x, origin_y) != (0, 0):
                metadata_text += (
                    f"Screenshot Origin: {origin} — image pixel (0, 0) is screen coordinate {origin}; "
                    f"add {origin} to every image pixel coordinate before passing to Click, Move, "
                    f"Scroll, or any loc= argument "
                    f"(e.g. image pixel (200, 150) → screen coordinate ({origin_x + 200}, {origin_y + 150}))\n"
                )
    if desktop_state.available_displays:
        metadata_text += "Visible Displays: "
        metadata_text += "; ".join(
            display_to_string(display) for display in desktop_state.available_displays
        )
        metadata_text += "\n"
    if desktop_state.screenshot_displays:
        metadata_text += f"Selected Displays: {','.join(str(index) for index in desktop_state.screenshot_displays)}\n"
    if desktop_state.screenshot_region:
        metadata_text += f"Screenshot Region: {desktop_state.screenshot_region.xyxy_to_string()}\n"
        metadata_text += "Coordinate Space: Virtual desktop coordinates\n"
        if include_ui_details and capture_result["use_ui_tree"]:
            metadata_text += (
                "UI Tree Scope: Region-filtered. Windows and UI elements outside this region "
                "are omitted. Omit region and display to inspect the full desktop.\n"
            )
    if desktop_state.screenshot_backend:
        metadata_text += f"Screenshot Backend: {desktop_state.screenshot_backend}\n"
    if saved_path:
        metadata_text += f"Screenshot Saved To: {saved_path}\n"
    if ui_detail_note:
        metadata_text += f"{ui_detail_note}\n"

    response_text = dedent(f'''
    {metadata_text}
    Active Desktop:
    {active_desktop}

    All Desktops:
    {all_desktops}

    Focused Window:
    {active_window}

    Opened Windows:
    {windows}
    ''')
    if include_ui_details:
        # The tree has no blank lines, and its notes (such as the truncation note) follow a
        # blank line at column 0: keep them out of dedent() so the tree loses only the
        # template's indent.
        tree, _, notes = (semantic_tree or "No elements found.").partition("\n\n")
        response_text += dedent(f'''

    UI Tree:
    {tree}''')
        if notes:
            response_text += f"\n\n{notes}"

    response = [response_text]
    if screenshot_bytes:
        response.append(Image(data=screenshot_bytes, format="png"))
    return response
