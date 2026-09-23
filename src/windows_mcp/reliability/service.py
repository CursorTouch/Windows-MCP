"""Reusable reliability helpers for single-turn desktop actions.

The helpers in this module deliberately depend only on the standard library and
the project's existing Windows UIA wrapper. They provide bounded retries,
forgiving target resolution for LLM-produced arguments, UI-tree validation,
and polling utilities that can be shared by Act and other tools.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, cast

import windows_mcp.uia as uia


T = TypeVar("T")
RetryOn = type[BaseException] | tuple[type[BaseException], ...]
RetryCallback = Callable[[int, BaseException | Any, float, Any], None]
Probe = Callable[[], bool | tuple[bool, Any]]


@dataclass(frozen=True)
class ResolvedTarget:
    """A normalized Act target.

    kind is either "label" or "point". Label targets retain the resolved
    UI-tree node so callers can inspect its current bounding box and metadata
    without another lookup.
    """

    kind: Literal["label", "point"]
    point: tuple[int, int]
    label: int | None = None
    node: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation for tool responses."""
        payload: dict[str, Any] = {"kind": self.kind, "point": list(self.point)}
        if self.label is not None:
            payload["label"] = self.label
        node = self.node
        if node is not None:
            payload.update(
                {
                    "name": getattr(node, "name", ""),
                    "control_type": getattr(node, "control_type", ""),
                    "window_name": getattr(node, "window_name", ""),
                    "bounding_box": _bounding_box_to_dict(getattr(node, "bounding_box", None)),
                }
            )
        return payload


def _bounding_box_to_dict(box: Any) -> dict[str, int] | None:
    if box is None:
        return None
    return {
        "left": int(getattr(box, "left", 0)),
        "top": int(getattr(box, "top", 0)),
        "right": int(getattr(box, "right", 0)),
        "bottom": int(getattr(box, "bottom", 0)),
        "width": int(getattr(box, "width", 0)),
        "height": int(getattr(box, "height", 0)),
    }


def _is_retry_result(value: Any) -> bool:
    return isinstance(value, tuple) and len(value) >= 2 and isinstance(value[0], bool)


def _normalize_retry_on(retry_on: RetryOn) -> tuple[type[BaseException], ...]:
    if isinstance(retry_on, tuple):
        return retry_on
    return (retry_on,)


def _retry_delay(base_delay: float, backoff: float, retry_index: int, jitter: bool) -> float:
    delay = base_delay * (backoff**retry_index)
    if jitter and delay > 0:
        delay *= random.uniform(0.5, 1.5)
    return max(0.0, delay)


def retry_call(
    fn: Callable[[], T],
    *,
    retries: int = 2,
    base_delay: float = 0.25,
    backoff: float = 2.0,
    jitter: bool = True,
    on_retry: RetryCallback | None = None,
    retry_on: RetryOn = (Exception,),
) -> T:
    """Call fn with bounded exponential backoff.

    retries counts retries after the initial call, so total attempts are
    retries + 1. A return value shaped like (ok, detail) is treated as a result
    signal: falsy ok is retried, and the final signal is returned unchanged.
    Exceptions in retry_on are retried and re-raised after the final attempt.
    """

    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError("retries must be a non-negative integer")
    if base_delay < 0:
        raise ValueError("base_delay must be non-negative")
    if backoff < 1:
        raise ValueError("backoff must be at least 1")

    retry_types = _normalize_retry_on(retry_on)
    total_attempts = retries + 1

    for attempt_index in range(total_attempts):
        try:
            result = fn()
        except BaseException as exc:
            if not isinstance(exc, retry_types) or attempt_index >= retries:
                raise
            delay = _retry_delay(base_delay, backoff, attempt_index, jitter)
            if on_retry is not None:
                on_retry(attempt_index + 1, exc, delay, None)
            if delay:
                time.sleep(delay)
            continue

        if not _is_retry_result(result):
            return result

        ok = bool(result[0])
        detail = result[1]
        if ok or attempt_index >= retries:
            return result

        delay = _retry_delay(base_delay, backoff, attempt_index, jitter)
        if on_retry is not None:
            on_retry(attempt_index + 1, detail, delay, result)
        if delay:
            time.sleep(delay)

    return cast(T, result)


def with_retry(
    *,
    retries: int = 2,
    base_delay: float = 0.25,
    backoff: float = 2.0,
    jitter: bool = True,
    on_retry: RetryCallback | None = None,
    retry_on: RetryOn = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator form of retry_call supporting sync and async callables."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                async_fn = cast(Callable[..., Awaitable[Any]], fn)
                if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
                    raise ValueError("retries must be a non-negative integer")
                if base_delay < 0:
                    raise ValueError("base_delay must be non-negative")
                if backoff < 1:
                    raise ValueError("backoff must be at least 1")
                retry_types = _normalize_retry_on(retry_on)

                for attempt_index in range(retries + 1):
                    try:
                        result = await async_fn(*args, **kwargs)
                    except BaseException as exc:
                        if not isinstance(exc, retry_types) or attempt_index >= retries:
                            raise
                        delay = _retry_delay(base_delay, backoff, attempt_index, jitter)
                        if on_retry is not None:
                            on_retry(attempt_index + 1, exc, delay, None)
                        if delay:
                            await asyncio.sleep(delay)
                        continue

                    if not _is_retry_result(result):
                        return result

                    ok = bool(result[0])
                    detail = result[1]
                    if ok or attempt_index >= retries:
                        return result

                    delay = _retry_delay(base_delay, backoff, attempt_index, jitter)
                    if on_retry is not None:
                        on_retry(attempt_index + 1, detail, delay, result)
                    if delay:
                        await asyncio.sleep(delay)

                return result

            return cast(Callable[..., T], async_wrapper)

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            return retry_call(
                lambda: fn(*args, **kwargs),
                retries=retries,
                base_delay=base_delay,
                backoff=backoff,
                jitter=jitter,
                on_retry=on_retry,
                retry_on=retry_on,
            )

        return sync_wrapper

    return decorator


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lstrip("+-").isdigit():
            return int(stripped)
    raise ValueError(f"{name} must be an integer")


def _as_point(value: Any, name: str = "target") -> tuple[int, int]:
    if isinstance(value, str):
        stripped = value.strip()
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} must be [x, y] or a JSON list") from exc
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must be a list of exactly 2 integers [x, y]")
    x = _as_int(value[0], f"{name}[0]")
    y = _as_int(value[1], f"{name}[1]")
    return x, y


def _node_from_label(desktop: Any, label: int) -> Any:
    state = getattr(desktop, "desktop_state", None)
    tree_state = getattr(state, "tree_state", None)
    if tree_state is None:
        raise ValueError("Desktop state is empty. Please call Snapshot first.")
    interactive_nodes = list(getattr(tree_state, "interactive_nodes", []))
    if label < len(interactive_nodes):
        return interactive_nodes[label]
    scroll_index = label - len(interactive_nodes)
    scrollable_nodes = list(getattr(tree_state, "scrollable_nodes", []))
    if 0 <= scroll_index < len(scrollable_nodes):
        return scrollable_nodes[scroll_index]
    raise IndexError(f"Label {label} is out of range")


def resolve_target(desktop: Any, target: Any) -> ResolvedTarget:
    """Resolve a label, coordinate pair, or stringified target.

    Supported forms are integer labels, "12", "label:12", [x, y],
    "[x, y]", {"label": 12}, and {"x": 10, "y": 20}.
    """

    if isinstance(target, ResolvedTarget):
        return target

    if isinstance(target, Mapping):
        if "label" in target:
            return resolve_target(desktop, target["label"])
        if "x" in target and "y" in target:
            return resolve_target(desktop, [target["x"], target["y"]])
        if "point" in target:
            return resolve_target(desktop, target["point"])
        raise ValueError("target mapping must contain label, point, or x/y")

    if isinstance(target, str):
        stripped = target.strip()
        if not stripped:
            raise ValueError("target must not be empty")
        lowered = stripped.casefold()
        if lowered.startswith("label:"):
            label = _as_int(stripped.split(":", 1)[1], "target label")
            return resolve_target(desktop, label)
        if stripped.startswith("["):
            return resolve_target(desktop, _as_point(stripped))
        if "," in stripped:
            parts = [part.strip() for part in stripped.split(",")]
            if len(parts) == 2:
                return resolve_target(desktop, parts)
        if stripped.lstrip("+-").isdigit():
            return resolve_target(desktop, int(stripped))
        raise ValueError(
            "target string must be a label number, 'label:12', '[x, y]', or 'x,y'"
        )

    if isinstance(target, bool):
        raise ValueError("target must be a label or coordinate pair, not a boolean")

    if isinstance(target, int):
        if target < 0:
            raise ValueError("target label must be non-negative")
        node = _node_from_label(desktop, target)
        center = getattr(node, "center", None)
        if center is None:
            raise ValueError(f"Label {target} has no center coordinate")
        return ResolvedTarget(
            kind="label",
            label=target,
            point=(int(center.x), int(center.y)),
            node=node,
        )

    if isinstance(target, (list, tuple)):
        return ResolvedTarget(kind="point", point=_as_point(target))

    raise ValueError("target must be an integer label, coordinate pair, or compatible string")


def element_alive(desktop: Any, label: int) -> bool:
    """Return whether label currently resolves in the cached UI tree."""
    try:
        _node_from_label(desktop, label)
    except (IndexError, ValueError, TypeError):
        return False
    return True


def _screen_bounds(desktop: Any) -> tuple[int, int, int, int]:
    try:
        box = desktop.get_screen_box()
        return int(box.left), int(box.top), int(box.right), int(box.bottom)
    except Exception:
        width, height = uia.GetScreenSize()
        return 0, 0, int(width), int(height)


def element_visible(desktop: Any, label: int) -> bool:
    """Return whether a label has a non-empty, on-screen bounding box."""
    try:
        node = _node_from_label(desktop, label)
        box = node.bounding_box
    except (AttributeError, IndexError, ValueError, TypeError):
        return False
    width = int(getattr(box, "width", 0))
    height = int(getattr(box, "height", 0))
    if width <= 0 or height <= 0:
        return False
    left, top, right, bottom = _screen_bounds(desktop)
    return not (
        box.right <= left
        or box.bottom <= top
        or box.left >= right
        or box.top >= bottom
    )


def element_enabled(desktop: Any, label: int) -> bool:
    """Return whether a label exists and is not explicitly marked disabled.

    The Windows MCP tree currently emits enabled interactive nodes, so
    tree membership is the primary signal. The metadata check protects future
    tree implementations that decide to retain disabled nodes.
    """
    try:
        node = _node_from_label(desktop, label)
    except (IndexError, ValueError, TypeError):
        return False
    metadata = getattr(node, "metadata", {}) or {}
    return bool(metadata.get("is_enabled", metadata.get("enabled", True)))


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _control_rect(control: Any) -> tuple[int, int, int, int] | None:
    try:
        rect = control.BoundingRectangle
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        return None


def _same_control(node: Any, control: Any) -> bool:
    node_name = str(getattr(node, "name", "") or "").strip().casefold()
    node_type = str(getattr(node, "control_type", "") or "").strip().casefold()
    control_name = str(_safe_attr(control, "Name", "") or "").strip().casefold()
    control_type = str(_safe_attr(control, "ControlTypeName", "") or "").strip().casefold()
    localized_type = str(_safe_attr(control, "LocalizedControlType", "") or "").strip().casefold()

    # An exact accessible name is the strongest identity signal. The tree uses
    # localized control-type labels while UIA may report an English type name,
    # so requiring both to match creates false occlusion failures.
    if node_name and control_name:
        return node_name == control_name
    if node_type and control_type and node_type != control_type:
        if not localized_type or node_type != localized_type:
            return False
    return bool(node_name or node_type)


def element_occluded(desktop: Any, label: int) -> tuple[bool, str]:
    """Check whether the point at label is covered by another UIA element.

    The check is conservative: an unresolvable top element is treated as an
    occlusion failure so Act retries after the next UI-tree refresh instead of
    clicking an unknown surface.
    """
    try:
        node = _node_from_label(desktop, label)
        point = (int(node.center.x), int(node.center.y))
    except Exception as exc:
        return True, f"cannot inspect occlusion for label {label}: {exc}"

    try:
        control = uia.ControlFromPoint(*point)
    except Exception as exc:
        return True, f"UIA point lookup failed at {point}: {exc}"

    if control is None:
        return True, f"no UIA element was found at {point}"
    if _same_control(node, control):
        return False, f"label {label} is the top element at {point}"

    control_name = _safe_attr(control, "Name", "")
    control_type = _safe_attr(control, "ControlTypeName", "")
    return (
        True,
        f"label {label} is covered at {point} by {control_type!r} {control_name!r}",
    )


def point_occluded(desktop: Any, point: Sequence[int]) -> tuple[bool, str]:
    """Return whether a raw coordinate is on-screen and has a UIA element."""
    x, y = int(point[0]), int(point[1])
    left, top, right, bottom = _screen_bounds(desktop)
    if not (left <= x < right and top <= y < bottom):
        return True, f"point ({x}, {y}) is outside the virtual desktop"
    try:
        control = uia.ControlFromPoint(x, y)
    except Exception as exc:
        return True, f"UIA point lookup failed at ({x}, {y}): {exc}"
    if control is None:
        return True, f"no UIA element was found at ({x}, {y})"
    return False, f"point ({x}, {y}) is reachable"


def wait_for_change(
    probe: Probe,
    *,
    timeout: float = 2.0,
    interval: float = 0.05,
) -> tuple[bool, float]:
    """Poll probe until it is truthy or timeout expires.

    Returns (changed, elapsed_seconds). A probe may also return (ok, detail);
    only the first item is used.
    """

    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    if interval <= 0:
        raise ValueError("interval must be positive")

    started = time.monotonic()
    while True:
        try:
            result = probe()
        except Exception:
            result = False
        if isinstance(result, tuple) and result:
            changed = bool(result[0])
        else:
            changed = bool(result)
        if changed:
            return True, time.monotonic() - started
        elapsed = time.monotonic() - started
        if elapsed >= timeout:
            return False, elapsed
        time.sleep(min(interval, max(0.0, timeout - elapsed)))
