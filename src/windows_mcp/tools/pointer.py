"""Pointer tool — stateful mouse down, move, up, and cancel actions."""

import json
from typing import Any, Literal, cast

from fastmcp import Context
from mcp.types import ToolAnnotations

from windows_mcp.desktop.pointer import (
    MouseButton,
    normalize_pointer_button,
    normalize_pointer_duration,
    normalize_pointer_point,
    normalize_pointer_timeout,
)
from windows_mcp.infrastructure import with_analytics


PointerAction = Literal["down", "move", "up", "cancel"]


def _normalize_action(value: object) -> PointerAction:
    if not isinstance(value, str):
        raise ValueError("action must be one of: down, move, up, cancel")
    normalized = value.strip().lower()
    if normalized not in {"down", "move", "up", "cancel"}:
        raise ValueError("action must be one of: down, move, up, cancel")
    return cast(PointerAction, normalized)


def _as_point(value: list[int] | str | None) -> tuple[int, int]:
    if value is None:
        raise ValueError("loc is required for Pointer down and move actions")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("loc must be a JSON list of exactly 2 integers [x, y]") from exc
    return normalize_pointer_point(value)


def register(mcp: Any, *, get_desktop, get_analytics) -> None:
    @mcp.tool(
        name="Pointer",
        description=(
            "Controls one stateful mouse-button gesture across calls. Use action='down' with loc, "
            "an optional left/right/middle button, and an automatic-release timeout that defaults "
            "to 30 seconds and is limited to 120 seconds; then action='move' one or more times with "
            "loc and optional duration; finish with action='up'. Use action='cancel' to release all "
            "mouse buttons and clear held state. Prefer Move with drag=True for a simple atomic drag "
            "in one call."
        ),
        annotations=ToolAnnotations(
            title="Pointer",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Pointer-Tool")
    def pointer_tool(
        action: Literal["down", "move", "up", "cancel"],
        loc: list[int] | str | None = None,
        button: Literal["left", "right", "middle"] | None = None,
        duration: float | int | str | None = None,
        timeout: float | int | str | None = None,
        ctx: Context = None,
    ) -> str:
        normalized_action = _normalize_action(action)

        if normalized_action == "down":
            if duration is not None:
                raise ValueError("duration is only supported for action='move'")
            point = _as_point(loc)
            requested_button = "left" if button is None else button
            normalized_button = cast(
                MouseButton,
                normalize_pointer_button(requested_button),
            )
            normalized_timeout = normalize_pointer_timeout(timeout)
            result = get_desktop().pointer_down(point, normalized_button, normalized_timeout)
            x, y = result["loc"]
            return (
                f"Pressed {result['button']} mouse button at ({x},{y}); "
                f"automatic release timeout is {result['timeout']:g} seconds."
            )

        if normalized_action == "move":
            if button is not None or timeout is not None:
                raise ValueError("button and timeout are only supported for action='down'")
            point = _as_point(loc)
            normalized_duration = normalize_pointer_duration(duration)
            result = get_desktop().pointer_move(point, normalized_duration)
            x, y = result["loc"]
            if result["duration"] is None:
                return f"Moved held {result['button']} mouse button to ({x},{y})."
            return (
                f"Moved held {result['button']} mouse button to ({x},{y}) "
                f"over {result['duration']:.3f} seconds."
            )

        if normalized_action == "up":
            if loc is not None or duration is not None or timeout is not None:
                raise ValueError("loc, duration, and timeout are not supported for action='up'")
            normalized_button = normalize_pointer_button(button, allow_none=True)
            result = get_desktop().pointer_up(normalized_button)
            return f"Released {result['button']} mouse button."

        if any(value is not None for value in (loc, button, duration, timeout)):
            raise ValueError(
                "loc, button, duration, and timeout are not supported for action='cancel'"
            )
        result = get_desktop().pointer_cancel()
        if result["button"] is None:
            return "Released all mouse buttons; no tracked button was held."
        return f"Released all mouse buttons and cleared held {result['button']} state."
