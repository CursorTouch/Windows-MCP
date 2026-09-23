"""MCP tools for key-state and human-like input.

The tools in this module are deliberately small wrappers around
HumanInputService. Register this module with register(mcp, ...) when composing
the Windows-MCP server.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

from fastmcp import Context
from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from windows_mcp.inputx.service import HumanInputService


def _as_number(value: object, name: str) -> float:
    """Coerce a number or stringified number to a finite float."""

    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _as_int(value: object, name: str) -> int:
    """Coerce a number or stringified integer to an int."""

    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not math.isfinite(parsed) or parsed != int(parsed):
        raise ValueError(f"{name} must be an integer")
    return int(parsed)


def _as_optional_int(value: object, name: str) -> int | None:
    """Coerce an optional integer value; empty strings mean None."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _as_int(value, name)


def _as_optional_float(value: object, name: str) -> float | None:
    """Coerce an optional number; empty strings mean None."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _as_number(value, name)


def _as_bool(value: object, name: str) -> bool:
    """Coerce common boolean representations."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{name} must be true or false")


def _as_keys(value: list[str] | str) -> list[str] | str:
    """Accept a list or a stringified JSON list, otherwise keep the chord string."""

    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("keys must be a list or '+'-separated string") from exc
        if not isinstance(parsed, list):
            raise ValueError("keys must be a list or '+'-separated string")
        return [str(item) for item in parsed]
    return text


def _resolve_label(desktop: Any, label: int) -> tuple[int, int]:
    """Resolve a UI label using the same desktop state convention as Click."""

    if desktop.desktop_state is None:
        raise ValueError("Desktop state is empty. Please call Snapshot first.")
    try:
        x, y = desktop.get_coordinates_from_label(label)
    except Exception as exc:
        raise ValueError(f"Failed to find element with label {label}: {exc}") from exc
    return int(x), int(y)


def register(
    mcp: Any,
    *,
    get_desktop: Callable[[], Any],
    get_analytics: Callable[[], Any],
) -> None:
    """Register key-state and human-like input tools on an MCP server."""

    service = HumanInputService()

    @mcp.tool(
        name="KeyDown",
        description=(
            "关键词: 键盘, key_down, 按下, 按住, 修饰键, 游戏操作. "
            "Press and hold a Windows key until KeyUp or ReleaseKeys is called."
        ),
        annotations=ToolAnnotations(
            title="KeyDown",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Input-KeyDown")
    def key_down_tool(key: str, ctx: Context = None) -> str:
        """Press a key down and leave it held."""

        canonical = service.key_down(key)
        return f"Key {canonical!r} is down."

    @mcp.tool(
        name="KeyUp",
        description=(
            "关键词: 键盘, key_up, 抬起, 释放, 修饰键, 防止卡键. "
            "Release a key previously pressed by KeyDown. Sends key-up even "
            "when this call is repeated."
        ),
        annotations=ToolAnnotations(
            title="KeyUp",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Input-KeyUp")
    def key_up_tool(key: str, ctx: Context = None) -> str:
        """Release a held key."""

        canonical = service.key_up(key)
        return f"Key {canonical!r} is up."

    @mcp.tool(
        name="Press",
        description=(
            "关键词: 键盘, 连按, press, 重复按键. Tap a key one or more times "
            "with a configurable interval. Use KeyDown/KeyUp for holding."
        ),
        annotations=ToolAnnotations(
            title="Press",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Input-Press")
    def press_tool(
        key: str,
        times: int | str = 1,
        interval: float | str = 0.05,
        ctx: Context = None,
    ) -> str:
        """Tap a key repeatedly."""

        count = _as_int(times, "times")
        service.press(key, times=count, interval=interval)
        return f"Pressed {key!r} {count} time(s)."

    @mcp.tool(
        name="Hotkey",
        description=(
            "关键词: 快捷键, 组合键, hotkey, win+r, alt+f4, ctrl+shift+esc. "
            "Press a chord using Win32 SendInput and release it in reverse order."
        ),
        annotations=ToolAnnotations(
            title="Hotkey",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Input-Hotkey")
    def hotkey_tool(keys: list[str] | str, ctx: Context = None) -> str:
        """Press a key chord such as 'ctrl+shift+esc'."""

        normalized_keys = _as_keys(keys)
        service.hotkey(normalized_keys)
        display = keys if isinstance(keys, str) else "+".join(keys)
        return f"Pressed hotkey {display!r}."

    @mcp.tool(
        name="Hold",
        description=(
            "关键词: 长按, hold, 按住, shift 长按, ctrl 长按. "
            "Press a key, keep it down for N seconds, and release it safely."
        ),
        annotations=ToolAnnotations(
            title="Hold",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Input-Hold")
    def hold_tool(key: str, seconds: float | int | str, ctx: Context = None) -> str:
        """Hold a key for a duration."""

        delay = _as_number(seconds, "seconds")
        service.hold(key, seconds=delay)
        return f"Held {key!r} for {delay:.3f} seconds."

    @mcp.tool(
        name="ReleaseKeys",
        description=(
            "关键词: 键盘, 释放全部, release_all, 防止卡键. Release every key "
            "tracked as held by this input service."
        ),
        annotations=ToolAnnotations(
            title="ReleaseKeys",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Input-ReleaseKeys")
    def release_keys_tool(ctx: Context = None) -> str:
        """Release all tracked held keys."""

        released = service.release_all()
        if not released:
            return "No tracked keys were held."
        return f"Released: {', '.join(released)}."

    @mcp.tool(
        name="TypeHuman",
        description=(
            "关键词: 拟人输入, human typing, 打字, 防风控, 间隔抖动, 错字纠正. "
            "Type text with variable inter-key delays, punctuation pauses, and "
            "optional typo-then-backspace corrections."
        ),
        annotations=ToolAnnotations(
            title="TypeHuman",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Input-TypeHuman")
    def type_human_tool(
        text: str,
        wpm: float | str = 60,
        jitter: float | str = 0.3,
        typo_rate: float | str = 0.0,
        ctx: Context = None,
    ) -> str:
        """Type text with human-like timing and optional corrections."""

        typed = service.type_human(text, wpm=wpm, jitter=jitter, typo_rate=typo_rate)
        return f"Typed {typed} character(s) with human-like timing."

    @mcp.tool(
        name="MoveHuman",
        description=(
            "关键词: 拟人移动, bezier, 贝塞尔, 鼠标轨迹, 防风控, easing. "
            "Move the cursor with a curved eased trajectory and optional overshoot "
            "correction instead of teleporting."
        ),
        annotations=ToolAnnotations(
            title="MoveHuman",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Input-MoveHuman")
    def move_human_tool(
        x: int | str,
        y: int | str,
        duration: float | int | str | None = None,
        curve: str = "bezier",
        overshoot: bool | str = True,
        ctx: Context = None,
    ) -> str:
        """Move the pointer using a human-like trajectory."""

        effective_duration = _as_optional_float(duration, "duration")
        use_overshoot = _as_bool(overshoot, "overshoot")
        end_x, end_y = service.move_human(
            x,
            y,
            duration=effective_duration,
            curve=curve,
            overshoot=use_overshoot,
        )
        return f"Moved human-like to ({end_x},{end_y})."

    @mcp.tool(
        name="ClickHuman",
        description=(
            "关键词: 拟人点击, human click, bezier, 按下抬起间隔, 防风控. "
            "Move with a human trajectory, pause, then click with a human mouse "
            "down/up hold time."
        ),
        annotations=ToolAnnotations(
            title="ClickHuman",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Input-ClickHuman")
    def click_human_tool(
        x: int | str | None = None,
        y: int | str | None = None,
        label: int | str | None = None,
        button: str = "left",
        ctx: Context = None,
    ) -> str:
        """Click at a coordinate, label, or the current cursor position."""

        target_x = _as_optional_int(x, "x")
        target_y = _as_optional_int(y, "y")
        if label is not None:
            if target_x is not None or target_y is not None:
                raise ValueError("provide either label or x/y, not both")
            target_x, target_y = _resolve_label(get_desktop(), _as_int(label, "label"))
        if (target_x is None) != (target_y is None):
            raise ValueError("x and y must both be provided or both omitted")
        end_x, end_y = service.click_human(target_x, target_y, button=button)
        return f"Human-clicked {button!r} at ({end_x},{end_y})."

    @mcp.tool(
        name="DragHuman",
        description=(
            "关键词: 拟人拖拽, drag, bezier, 贝塞尔, 防风控. Move to a start "
            "point, hold the mouse button, follow a human trajectory, and release."
        ),
        annotations=ToolAnnotations(
            title="DragHuman",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Input-DragHuman")
    def drag_human_tool(
        from_x: int | str,
        from_y: int | str,
        to_x: int | str,
        to_y: int | str,
        duration: float | int | str | None = None,
        button: str = "left",
        ctx: Context = None,
    ) -> str:
        """Drag between coordinates with a human-like path."""

        effective_duration = _as_optional_float(duration, "duration")
        end_x, end_y = service.drag_human(
            from_x,
            from_y,
            to_x,
            to_y,
            duration=effective_duration,
            button=button,
        )
        return f"Human-dragged to ({end_x},{end_y})."


__all__ = ["register"]
