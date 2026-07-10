"""Window tool - exact window discovery, activation, and bounds."""

import json
from typing import Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics


def _as_bounds(value: list[int] | str | None) -> list[int] | None:
    if value is None or isinstance(value, list):
        bounds = value
    else:
        bounds = json.loads(value)
    if bounds is not None and len(bounds) != 4:
        raise ValueError("Bounds must be a list of exactly 4 integers [x, y, width, height]")
    return bounds


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="Window",
        description=(
            "Find, activate, or set bounds for exact windows without fuzzy matching. "
            "Modes: find, activate, bounds. Use title/title_match, process, process_id, "
            "and handle to narrow identity. Bounds are [x, y, width, height]."
        ),
        annotations=ToolAnnotations(
            title="Window",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Window-Tool")
    def window_tool(
        mode: Literal["find", "activate", "bounds"] = "find",
        title: str | None = None,
        title_match: Literal["exact", "contains"] = "contains",
        process: str | None = None,
        process_id: int | None = None,
        handle: int | None = None,
        outer: list[int] | str | None = None,
        client: list[int] | str | None = None,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        outer = _as_bounds(outer)
        client = _as_bounds(client)

        match mode:
            case "find":
                windows = desktop.find_exact_windows(
                    title=title,
                    title_match=title_match,
                    process=process,
                    process_id=process_id,
                    handle=handle,
                )
                return json.dumps({"windows": windows, "count": len(windows)}, indent=2)
            case "activate":
                if handle is None:
                    raise ValueError("handle is required when mode is activate")
                window = desktop.activate_exact_window(
                    handle=handle,
                    process_id=process_id,
                    process=process,
                    title=title,
                    title_match=title_match,
                )
                return json.dumps({"activated": window}, indent=2)
            case "bounds":
                if handle is None:
                    raise ValueError("handle is required when mode is bounds")
                window = desktop.set_exact_window_bounds(
                    handle=handle,
                    outer=outer,
                    client=client,
                    process_id=process_id,
                    process=process,
                    title=title,
                    title_match=title_match,
                )
                return json.dumps({"window": window}, indent=2)

        raise ValueError('mode must be one of "find", "activate", or "bounds"')
