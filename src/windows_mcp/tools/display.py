"""DisplayInventory tool - read-only display and DPI metadata."""

from collections.abc import Callable
from typing import Any

from fastmcp import Context
from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics


def _rect_to_dict(rect: Any) -> dict[str, int] | None:
    if rect is None:
        return None
    return {
        "left": rect.left,
        "top": rect.top,
        "right": rect.right,
        "bottom": rect.bottom,
        "width": rect.width(),
        "height": rect.height(),
    }


def register(
    mcp: Any,
    *,
    get_desktop: Callable[[], Any],
    get_analytics: Callable[[], Any],
) -> None:
    @mcp.tool(
        name="DisplayInventory",
        description=(
            "Read active display layout and DPI metadata. Reports display index, device name, "
            "monitor/work-area bounds, resolution, orientation, primary flag, effective DPI, "
            "and scale."
        ),
        annotations=ToolAnnotations(
            title="DisplayInventory",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "DisplayInventory-Tool")
    def display_inventory_tool(ctx: Context = None) -> list[dict[str, object]]:
        displays = get_desktop().get_displays()
        return [
            {
                "index": display.index,
                "device": display.device_name,
                "primary": display.primary,
                "bounds": _rect_to_dict(display.rect),
                "work_area": _rect_to_dict(getattr(display, "work_rect", None)),
                "resolution": f"{display.rect.width()}x{display.rect.height()}",
                "orientation": getattr(display, "orientation", None),
                "effective_dpi": getattr(display, "effective_dpi", None),
                "scale": getattr(display, "scale", None),
            }
            for display in displays
        ]
