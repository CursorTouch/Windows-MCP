"""DisplayInventory tool - read-only display and DPI metadata."""

import json

from fastmcp import Context
from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics


def _rect_to_dict(rect) -> dict[str, int] | None:
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


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="DisplayInventory",
        description=(
            "Read active display layout and DPI metadata. Reports display index, device name, "
            "monitor/work-area rectangles, primary flag, effective DPI, and scale."
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
    def display_inventory_tool(ctx: Context = None) -> str:
        displays = get_desktop().get_displays()
        payload = {
            "displays": [
                {
                    "index": display.index,
                    "device_name": display.device_name,
                    "primary": display.primary,
                    "rect": _rect_to_dict(display.rect),
                    "work_rect": _rect_to_dict(getattr(display, "work_rect", None)),
                    "effective_dpi": getattr(display, "effective_dpi", None),
                    "scale": getattr(display, "scale", None),
                }
                for display in displays
            ],
            "count": len(displays),
        }
        return json.dumps(payload, indent=2)
