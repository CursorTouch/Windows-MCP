"""Act tool - one-call action, verification, and bounded retry.

Act is intentionally a single-turn primitive: it resolves an element reference
at action time, executes one operation, waits briefly, verifies the result, and
retries with a fresh tree lookup when needed. This avoids the common
Snapshot -> Click -> Snapshot loop for straightforward UI actions.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from windows_mcp.infrastructure import with_analytics
from windows_mcp.reliability import (
    ResolvedTarget,
    element_alive,
    element_enabled,
    element_occluded,
    element_visible,
    point_occluded,
    resolve_target,
    retry_call,
    wait_for_change,
)


Operation = Literal[
    "CLICK",
    "DOUBLE_CLICK",
    "RIGHT_CLICK",
    "HOVER",
    "TYPE_TEXT",
    "SELECT",
    "SCROLL_UP",
    "SCROLL_DOWN",
    "PRESS",
    "FOCUS",
    "WAIT",
    "DONE",
]

VerifyInput = dict[str, Any] | str | None


@dataclass(frozen=True)
class _NodeSnapshot:
    name: str
    control_type: str
    window_name: str
    bounding_box: tuple[int, int, int, int] | None
    value: str
    focused: bool
    toggle_state: str
    selected: str
    expand_state: str


@dataclass(frozen=True)
class _Baseline:
    active_window: str
    target_label: int | None
    target: _NodeSnapshot | None
    signature: tuple[Any, ...]


@dataclass(frozen=True)
class _Check:
    ok: bool
    detail: str


@dataclass(frozen=True)
class _TargetAnchor:
    kind: str
    label: int | None
    point: tuple[int, int]
    name: str
    control_type: str
    window_name: str
    bounding_box: tuple[int, int, int, int] | None


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value.strip())
    raise ValueError(f"{name} must be an integer")


def _as_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be a finite number")
    return number


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"{name} must be true or false")


def _parse_verify_value(value: str) -> Any:
    stripped = value.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _parse_verify(verify: VerifyInput) -> dict[str, Any]:
    """Normalize verify into a condition mapping.

    Accepts a mapping, a JSON object string, or simple key=value text such as
    text_contains=hello,value_changed=true. A plain string without '=' is
    treated as text_contains.
    """

    if verify is None:
        return {}
    if isinstance(verify, Mapping):
        return dict(verify)
    if not isinstance(verify, str):
        raise ValueError("verify must be a mapping or string")
    stripped = verify.strip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError("verify JSON must be an object")
        return parsed
    parts = [part.strip() for part in stripped.split(",") if part.strip()]
    if parts and all("=" in part for part in parts):
        conditions: dict[str, Any] = {}
        for part in parts:
            key, value = part.split("=", 1)
            key = key.strip()
            if not key:
                raise ValueError("verify key must not be empty")
            conditions[key] = _parse_verify_value(value)
        return conditions
    return {"text_contains": stripped}


def _refresh_state(desktop: Any) -> Any:
    return desktop.get_state(
        use_vision=False,
        use_dom=False,
        use_ui_tree=True,
        use_annotation=False,
    )


def _iter_nodes(state: Any) -> Iterator[Any]:
    tree_state = getattr(state, "tree_state", None)
    if tree_state is None:
        return
    yield from getattr(tree_state, "interactive_nodes", [])
    yield from getattr(tree_state, "scrollable_nodes", [])


def _iter_text_sources(state: Any) -> Iterator[str]:
    active_window = getattr(state, "active_window", None)
    if active_window is not None:
        yield str(getattr(active_window, "name", ""))

    for window in getattr(state, "windows", []):
        yield str(getattr(window, "name", ""))

    for node in _iter_nodes(state):
        yield str(getattr(node, "name", ""))
        yield str(getattr(node, "control_type", ""))
        yield str(getattr(node, "window_name", ""))
        for value in (getattr(node, "metadata", {}) or {}).values():
            yield str(value)

    tree_state = getattr(state, "tree_state", None)
    if tree_state is not None:
        for node in getattr(tree_state, "dom_informative_nodes", []):
            yield str(getattr(node, "text", ""))


def _contains_text(state: Any, expected: Any) -> bool:
    needle = str(expected).casefold()
    return any(needle in source.casefold() for source in _iter_text_sources(state))


def _state_signature(state: Any) -> tuple[Any, ...]:
    active_window = getattr(state, "active_window", None)
    active_name = str(getattr(active_window, "name", "")) if active_window else ""
    window_names = tuple(str(getattr(window, "name", "")) for window in getattr(state, "windows", []))
    nodes = []
    for node in list(_iter_nodes(state))[:300]:
        metadata = getattr(node, "metadata", {}) or {}
        box = getattr(node, "bounding_box", None)
        nodes.append(
            (
                str(getattr(node, "name", "")),
                str(getattr(node, "control_type", "")),
                str(getattr(node, "window_name", "")),
                (
                    int(getattr(box, "left", 0)),
                    int(getattr(box, "top", 0)),
                    int(getattr(box, "right", 0)),
                    int(getattr(box, "bottom", 0)),
                )
                if box is not None
                else None,
                str(metadata.get("value", "")),
                bool(metadata.get("has_focused", False)),
                str(metadata.get("toggle_state", "")),
                str(metadata.get("is_selected", "")),
                str(metadata.get("selection", "")),
                str(metadata.get("expand_collapse_state", "")),
            )
        )
    tree_state = getattr(state, "tree_state", None)
    dom_text = ()
    if tree_state is not None:
        dom_text = tuple(
            str(getattr(node, "text", ""))
            for node in getattr(tree_state, "dom_informative_nodes", [])[:100]
        )
    return active_name, window_names, tuple(nodes), dom_text


def _node_snapshot(node: Any | None) -> _NodeSnapshot | None:
    if node is None:
        return None
    metadata = getattr(node, "metadata", {}) or {}
    box = getattr(node, "bounding_box", None)
    box_tuple = None
    if box is not None:
        box_tuple = (
            int(getattr(box, "left", 0)),
            int(getattr(box, "top", 0)),
            int(getattr(box, "right", 0)),
            int(getattr(box, "bottom", 0)),
        )
    return _NodeSnapshot(
        name=str(getattr(node, "name", "")),
        control_type=str(getattr(node, "control_type", "")),
        window_name=str(getattr(node, "window_name", "")),
        bounding_box=box_tuple,
        value=str(metadata.get("value", "")),
        focused=bool(metadata.get("has_focused", False)),
        toggle_state=str(metadata.get("toggle_state", "")),
        selected=str(metadata.get("is_selected", "")),
        expand_state=str(metadata.get("expand_collapse_state", "")),
    )


def _capture_baseline(desktop: Any, target: ResolvedTarget | None) -> _Baseline:
    state = getattr(desktop, "desktop_state", None)
    active_window = getattr(state, "active_window", None)
    active_name = str(getattr(active_window, "name", "")) if active_window else ""
    target_label = target.label if target is not None else None
    target_snapshot = _node_snapshot(target.node) if target is not None else None
    return _Baseline(
        active_window=active_name,
        target_label=target_label,
        target=target_snapshot,
        signature=_state_signature(state),
    )


def _indexed_nodes(state: Any) -> Iterator[tuple[int, Any]]:
    tree_state = getattr(state, "tree_state", None)
    if tree_state is None:
        return
    interactive = list(getattr(tree_state, "interactive_nodes", []))
    for index, node in enumerate(interactive):
        yield index, node
    for offset, node in enumerate(getattr(tree_state, "scrollable_nodes", [])):
        yield len(interactive) + offset, node


def _anchor_from_resolved(resolved: ResolvedTarget | None) -> _TargetAnchor | None:
    if resolved is None:
        return None
    node = resolved.node
    box = getattr(node, "bounding_box", None) if node is not None else None
    bbox = None
    if box is not None:
        bbox = (
            int(getattr(box, "left", 0)),
            int(getattr(box, "top", 0)),
            int(getattr(box, "right", 0)),
            int(getattr(box, "bottom", 0)),
        )
    return _TargetAnchor(
        kind=resolved.kind,
        label=resolved.label,
        point=resolved.point,
        name=str(getattr(node, "name", "")) if node is not None else "",
        control_type=str(getattr(node, "control_type", "")) if node is not None else "",
        window_name=str(getattr(node, "window_name", "")) if node is not None else "",
        bounding_box=bbox,
    )


def _capture_anchor(desktop: Any, target: Any) -> _TargetAnchor | None:
    if target is None or getattr(desktop, "desktop_state", None) is None:
        return None
    try:
        return _anchor_from_resolved(resolve_target(desktop, target))
    except Exception:
        return None


def _node_matches_anchor(node: Any, anchor: _TargetAnchor) -> bool:
    name = str(getattr(node, "name", "")).strip().casefold()
    control_type = str(getattr(node, "control_type", "")).strip().casefold()
    window_name = str(getattr(node, "window_name", "")).strip().casefold()
    anchor_name = anchor.name.strip().casefold()
    anchor_type = anchor.control_type.strip().casefold()
    anchor_window = anchor.window_name.strip().casefold()
    if anchor_name and name != anchor_name:
        return False
    if anchor_type and control_type != anchor_type:
        return False
    if anchor_window and window_name != anchor_window:
        return False
    return bool(anchor_name or anchor_type or anchor_window)


def _resolve_target_with_anchor(
    desktop: Any,
    target: Any,
    anchor: _TargetAnchor | None,
) -> ResolvedTarget:
    resolved = resolve_target(desktop, target)
    if anchor is None or anchor.kind != "label":
        return resolved
    if _node_matches_anchor(resolved.node, anchor):
        return resolved

    candidates: list[tuple[int, Any]] = []
    state = getattr(desktop, "desktop_state", None)
    for label, node in _indexed_nodes(state):
        if _node_matches_anchor(node, anchor):
            candidates.append((label, node))
    if not candidates:
        raise ValueError(
            f"target anchor {anchor.name!r} ({anchor.control_type!r}) no longer exists"
        )

    def distance(item: tuple[int, Any]) -> int:
        node = item[1]
        x, y = int(node.center.x), int(node.center.y)
        return (x - anchor.point[0]) ** 2 + (y - anchor.point[1]) ** 2

    label, node = min(candidates, key=distance)
    return ResolvedTarget(
        kind="label",
        label=label,
        point=(int(node.center.x), int(node.center.y)),
        node=node,
    )


def _snapshot_matches_node(snapshot: _NodeSnapshot | None, node: Any) -> bool:
    if snapshot is None:
        return False
    current = _node_snapshot(node)
    if current is None:
        return False
    if snapshot.name and snapshot.name.casefold() != current.name.casefold():
        return False
    if snapshot.control_type and snapshot.control_type.casefold() != current.control_type.casefold():
        return False
    if snapshot.window_name and snapshot.window_name.casefold() != current.window_name.casefold():
        return False
    return bool(snapshot.name or snapshot.control_type or snapshot.window_name)


def _find_node_by_snapshot(desktop: Any, snapshot: _NodeSnapshot | None) -> Any | None:
    state = getattr(desktop, "desktop_state", None)
    matches = [node for _, node in _indexed_nodes(state) if _snapshot_matches_node(snapshot, node)]
    if not matches:
        return None
    if snapshot is None or snapshot.bounding_box is None:
        return matches[0]
    left, top, right, bottom = snapshot.bounding_box
    target_x = (left + right) // 2
    target_y = (top + bottom) // 2
    return min(
        matches,
        key=lambda node: (
            (int(node.center.x) - target_x) ** 2
            + (int(node.center.y) - target_y) ** 2
        ),
    )


def _resolve_for_verification(
    desktop: Any,
    target: Any,
    anchor: _TargetAnchor | None,
) -> ResolvedTarget | None:
    if target is None:
        return None
    try:
        return _resolve_target_with_anchor(desktop, target, anchor)
    except Exception:
        return None


def _value_changed(desktop: Any, baseline: _Baseline) -> bool:
    if baseline.target is not None:
        current = _find_node_by_snapshot(desktop, baseline.target)
        if current is None:
            return True
        if _node_snapshot(current) != baseline.target:
            return True
    return _state_signature(getattr(desktop, "desktop_state", None)) != baseline.signature


def _match_node(node: Any, value: Any) -> bool:
    needle = str(value).casefold()
    metadata = getattr(node, "metadata", {}) or {}
    candidates = [
        getattr(node, "name", ""),
        getattr(node, "control_type", ""),
        getattr(node, "window_name", ""),
        *metadata.values(),
    ]
    return any(needle in str(candidate).casefold() for candidate in candidates)


def _find_node(desktop: Any, value: Any) -> Any | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) or (
        isinstance(value, str)
        and (value.strip().lstrip("+-").isdigit() or value.strip().casefold().startswith("label:"))
    ):
        try:
            resolved = resolve_target(desktop, value)
            return resolved.node
        except Exception:
            return None
    state = getattr(desktop, "desktop_state", None)
    for node in _iter_nodes(state):
        if _match_node(node, value):
            return node
    return None


def _node_value(node: Any | None) -> str:
    if node is None:
        return ""
    return str((getattr(node, "metadata", {}) or {}).get("value", ""))


def _node_focused(node: Any | None) -> bool:
    if node is None:
        return False
    return bool((getattr(node, "metadata", {}) or {}).get("has_focused", False))


def _target_node(desktop: Any, target: ResolvedTarget | None) -> Any | None:
    del desktop
    if target is None or target.kind != "label":
        return None
    return target.node


def _default_verification(
    desktop: Any,
    operation: str,
    target: ResolvedTarget | None,
    text: str | None,
    baseline: _Baseline,
) -> _Check:
    changed = _value_changed(desktop, baseline)
    if operation == "DONE":
        return _Check(True, "DONE is a no-op")
    if operation == "WAIT":
        return _Check(True, "WAIT completed")
    if operation == "TYPE_TEXT" and text:
        if _contains_text(getattr(desktop, "desktop_state", None), text):
            return _Check(True, f"text {text!r} appeared in the UI tree")
        if text.casefold() in _node_value(_target_node(desktop, target)).casefold():
            return _Check(True, f"target value contains {text!r}")
        if changed:
            return _Check(True, "target state changed after typing")
        return _Check(False, "text was not found in the refreshed UI tree")
    if changed:
        return _Check(True, "observable UI state changed")
    return _Check(False, "action executed but no observable UI state change was detected")


def _all_values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _element_check(desktop: Any, value: Any, check_name: str) -> bool:
    node = _find_node(desktop, value)
    if node is None:
        return False
    label = None
    state = getattr(desktop, "desktop_state", None)
    for index, current in _indexed_nodes(state):
        if current is node:
            label = index
            break
    if check_name == "element_enabled":
        return element_enabled(desktop, label) if label is not None else True
    if check_name == "element_visible":
        return element_visible(desktop, label) if label is not None else True
    if check_name in {"element_focused", "focused_element"}:
        return _node_focused(node)
    return True


def _verify_mapping(
    desktop: Any,
    verify: Mapping[str, Any],
    baseline: _Baseline,
    operation: str,
    text: str | None,
    target: ResolvedTarget | None,
) -> _Check:
    if not verify:
        return _default_verification(desktop, operation, target, text, baseline)

    details: list[str] = []
    for raw_key, expected in verify.items():
        key = str(raw_key).strip().casefold().replace("-", "_")
        if key in {"text_contains", "text_not_contains"}:
            values = _all_values(expected)
            matches = [_contains_text(getattr(desktop, "desktop_state", None), item) for item in values]
            passed = all(matches) if key == "text_contains" else not any(matches)
            details.append(f"{key}={expected!r}: {passed}")
            if not passed:
                return _Check(False, "; ".join(details))
        elif key in {"element_exists", "element_gone", "element_enabled", "element_visible"}:
            values = _all_values(expected)
            checks = [_element_check(desktop, item, key) for item in values]
            passed = all(checks) if key != "element_gone" else not any(checks)
            details.append(f"{key}={expected!r}: {passed}")
            if not passed:
                return _Check(False, "; ".join(details))
        elif key in {"focused_element", "element_focused"}:
            values = _all_values(expected)
            passed = all(_element_check(desktop, item, "focused_element") for item in values)
            details.append(f"{key}={expected!r}: {passed}")
            if not passed:
                return _Check(False, "; ".join(details))
        elif key in {"value_changed", "changed", "tree_changed"}:
            expected_bool = _as_bool(expected, key)
            changed = _value_changed(desktop, baseline)
            passed = changed == expected_bool
            details.append(f"{key}={expected_bool}: {passed}")
            if not passed:
                return _Check(False, "; ".join(details))
        elif key in {"value_contains", "value_equals"}:
            current_value = _node_value(_target_node(desktop, target))
            values = _all_values(expected)
            if key == "value_contains":
                passed = all(str(item).casefold() in current_value.casefold() for item in values)
            else:
                passed = current_value.casefold() in {
                    str(item).casefold() for item in values
                }
            details.append(f"{key}={expected!r}: {passed}")
            if not passed:
                return _Check(False, "; ".join(details))
        elif key == "active_window_contains":
            state = getattr(desktop, "desktop_state", None)
            active = getattr(state, "active_window", None)
            active_name = str(getattr(active, "name", "")) if active else ""
            values = _all_values(expected)
            passed = all(str(item).casefold() in active_name.casefold() for item in values)
            details.append(f"active_window_contains={expected!r}: {passed}")
            if not passed:
                return _Check(False, "; ".join(details))
        elif key == "window_changed":
            expected_bool = _as_bool(expected, key)
            state = getattr(desktop, "desktop_state", None)
            active = getattr(state, "active_window", None)
            current_active = str(getattr(active, "name", "")) if active else ""
            changed = current_active != baseline.active_window
            passed = changed == expected_bool
            details.append(f"window_changed={expected_bool}: {passed}")
            if not passed:
                return _Check(False, "; ".join(details))
        else:
            return _Check(False, f"unsupported verify condition: {raw_key}")

    return _Check(True, "; ".join(details) if details else "verification passed")


def _validate_resolved(desktop: Any, resolved: ResolvedTarget) -> _Check:
    if resolved.kind == "label":
        label = resolved.label
        if label is None:
            return _Check(False, "resolved label is missing")
        if not element_alive(desktop, label):
            return _Check(False, f"label {label} no longer exists")
        if not element_visible(desktop, label):
            return _Check(False, f"label {label} is not visible on-screen")
        if not element_enabled(desktop, label):
            return _Check(False, f"label {label} is disabled")
        occluded, detail = element_occluded(desktop, label)
        if occluded:
            return _Check(False, detail)
        return _Check(True, detail)
    occluded, detail = point_occluded(desktop, resolved.point)
    if occluded:
        return _Check(False, detail)
    return _Check(True, detail)


def _post_action_timeout(operation: str, timeout: float) -> float:
    if operation == "TYPE_TEXT":
        return min(timeout, 0.2)
    if operation == "WAIT":
        return timeout
    return min(timeout, 0.05)


def _execute_action(
    desktop: Any,
    operation: str,
    resolved: ResolvedTarget,
    text: str | None,
    button: str,
) -> str:
    point = resolved.point
    if operation == "CLICK":
        desktop.click(point, button=button, clicks=1)
        return f"clicked {button} at {point}"
    if operation == "DOUBLE_CLICK":
        desktop.click(point, button=button, clicks=2)
        return f"double-clicked {button} at {point}"
    if operation == "RIGHT_CLICK":
        desktop.click(point, button="right", clicks=1)
        return f"right-clicked at {point}"
    if operation == "HOVER":
        desktop.click(point, button=button, clicks=0)
        return f"hovered at {point}"
    if operation in {"SELECT", "FOCUS"}:
        desktop.click(point, button="left", clicks=1)
        return f"{operation.lower()}ed at {point}"
    if operation == "TYPE_TEXT":
        if text is None:
            raise ValueError("text is required for TYPE_TEXT")
        desktop.type(point, text=text, clear=False, caret_position="idle", press_enter=False)
        return f"typed {text!r} at {point}"
    if operation == "SCROLL_UP":
        desktop.scroll(point, "vertical", "up", 1)
        return f"scrolled up at {point}"
    if operation == "SCROLL_DOWN":
        desktop.scroll(point, "vertical", "down", 1)
        return f"scrolled down at {point}"
    if operation == "PRESS":
        if text is None:
            raise ValueError("text is required for PRESS")
        if resolved.kind == "label":
            desktop.click(point, button="left", clicks=1)
        desktop.shortcut(text)
        return f"pressed {text!r}"
    raise ValueError(f"unsupported operation for execution: {operation}")


def _type_text_already_satisfied(
    desktop: Any,
    resolved: ResolvedTarget,
    text: str,
) -> bool:
    if resolved.kind != "label":
        return False
    node = _target_node(desktop, resolved)
    if node is None:
        return False
    return text.casefold() in _node_value(node).casefold()


def _run_attempt(
    desktop: Any,
    *,
    operation: str,
    target: Any,
    anchor: _TargetAnchor | None,
    text: str | None,
    verify: dict[str, Any],
    timeout: float,
    button: str,
    attempt_number: int,
) -> tuple[bool, dict[str, Any]]:
    payload: dict[str, Any] = {
        "operation": operation,
        "resolved_target": None,
        "verified": False,
        "detail": "",
    }
    try:
        if operation == "DONE":
            _refresh_state(desktop)
            try:
                resolved = (
                    _resolve_target_with_anchor(desktop, target, anchor)
                    if target is not None
                    else None
                )
            except Exception:
                resolved = None
            payload["resolved_target"] = resolved.to_dict() if resolved else None
            baseline = _capture_baseline(desktop, resolved)
            verification = _verify_mapping(desktop, verify, baseline, operation, text, resolved)
            payload["verified"] = verification.ok
            payload["detail"] = verification.detail
            return verification.ok, payload

        if operation == "WAIT":
            return _run_wait(
                desktop,
                target=target,
                anchor=anchor,
                verify=verify,
                timeout=timeout,
                text=text,
                payload=payload,
            )

        _refresh_state(desktop)
        resolved = _resolve_target_with_anchor(desktop, target, anchor)
        payload["resolved_target"] = resolved.to_dict()
        validation = _validate_resolved(desktop, resolved)
        if not validation.ok:
            payload["detail"] = validation.detail
            return False, payload

        baseline = _capture_baseline(desktop, resolved)
        if (
            attempt_number > 1
            and operation == "TYPE_TEXT"
            and text
            and _type_text_already_satisfied(desktop, resolved, text)
        ):
            payload["verified"] = True
            payload["detail"] = f"text {text!r} was already present; skipped duplicate retry"
            return True, payload

        action_detail = _execute_action(desktop, operation, resolved, text, button)
        post_timeout = _post_action_timeout(operation, timeout)
        interval = max(0.005, min(0.025, post_timeout / 2))

        def probe() -> bool:
            _refresh_state(desktop)
            current = _resolve_for_verification(desktop, target, anchor)
            result = _verify_mapping(
                desktop,
                verify,
                baseline,
                operation,
                text,
                current,
            )
            return result.ok

        changed, waited = wait_for_change(probe, timeout=post_timeout, interval=interval)
        _refresh_state(desktop)
        current = _resolve_for_verification(desktop, target, anchor)
        verification = _verify_mapping(
            desktop,
            verify,
            baseline,
            operation,
            text,
            current,
        )
        payload["verified"] = verification.ok
        if current is not None:
            payload["resolved_target"] = current.to_dict()
        payload["detail"] = (
            f"{action_detail}; verification={verification.detail}; "
            f"bounded_wait={waited * 1000:.1f}ms changed={changed}"
        )
        if verify:
            return verification.ok, payload
        return True, payload
    except Exception as exc:
        payload["detail"] = f"{type(exc).__name__}: {exc}"
        return False, payload


def _run_wait(
    desktop: Any,
    *,
    target: Any,
    anchor: _TargetAnchor | None,
    verify: dict[str, Any],
    timeout: float,
    text: str | None,
    payload: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    if target is None:
        time.sleep(timeout)
        payload["verified"] = True
        payload["detail"] = f"waited {timeout:.3f}s"
        return True, payload

    latest: dict[str, ResolvedTarget | None] = {"resolved": None}

    def probe() -> bool:
        _refresh_state(desktop)
        try:
            resolved = _resolve_target_with_anchor(desktop, target, anchor)
        except Exception:
            return False
        latest["resolved"] = resolved
        return _validate_resolved(desktop, resolved).ok

    changed, elapsed = wait_for_change(probe, timeout=timeout, interval=0.05)
    if not changed:
        payload["detail"] = f"WAIT timed out after {elapsed:.3f}s waiting for target {target!r}"
        return False, payload

    _refresh_state(desktop)
    resolved = _resolve_target_with_anchor(desktop, target, anchor)
    payload["resolved_target"] = resolved.to_dict()
    baseline = _capture_baseline(desktop, resolved)
    verification = _verify_mapping(desktop, verify, baseline, "WAIT", text, resolved)
    payload["verified"] = verification.ok
    payload["detail"] = f"target became actionable after {elapsed:.3f}s; {verification.detail}"
    return verification.ok, payload


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="Act",
        description=(
            "关键词: 单轮动作, act, click, type, verify, retry, UI action. "
            "Resolve an element, execute one action, verify the result, and retry "
            "with a fresh UI-tree lookup inside a single call. Operations: CLICK, "
            "DOUBLE_CLICK, RIGHT_CLICK, HOVER, TYPE_TEXT, SELECT, SCROLL_UP, "
            "SCROLL_DOWN, PRESS, FOCUS, WAIT, DONE. target accepts a UIA label, "
            "[x, y], 'label:12', or a stringified list. verify accepts a mapping "
            "such as {'text_contains': 'hello'}, {'element_exists': 12}, "
            "{'value_changed': true}, or a compact string like "
            "'text_contains=hello,value_changed=true'."
        ),
        annotations=ToolAnnotations(
            title="Act",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Act-Tool")
    def act_tool(
        operation: Operation,
        target: Any = None,
        text: str | None = None,
        verify: VerifyInput = None,
        retries: int = 2,
        timeout: float = 5.0,
        button: str = "left",
        ctx: Context = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        operation_name = str(operation).strip().upper()
        valid_operations = {
            "CLICK",
            "DOUBLE_CLICK",
            "RIGHT_CLICK",
            "HOVER",
            "TYPE_TEXT",
            "SELECT",
            "SCROLL_UP",
            "SCROLL_DOWN",
            "PRESS",
            "FOCUS",
            "WAIT",
            "DONE",
        }
        if operation_name not in valid_operations:
            return {
                "ok": False,
                "operation": operation_name,
                "resolved_target": None,
                "elapsed_ms": 0.0,
                "verified": False,
                "attempts": 0,
                "detail": f"unsupported operation: {operation}",
            }

        try:
            retry_count = _as_int(retries, "retries")
            if retry_count < 0 or retry_count > 5:
                raise ValueError("retries must be between 0 and 5")
            timeout_value = _as_float(timeout, "timeout")
            if timeout_value <= 0 or timeout_value > 30:
                raise ValueError("timeout must be greater than 0 and at most 30 seconds")
            normalized_button = str(button).strip().casefold()
            if normalized_button not in {"left", "right", "middle"}:
                raise ValueError("button must be left, right, or middle")
            verify_conditions = _parse_verify(verify)
            if operation_name == "TYPE_TEXT" and text is None:
                raise ValueError("text is required for TYPE_TEXT")
            if operation_name == "PRESS" and text is None:
                raise ValueError("text is required for PRESS")
            if operation_name not in {"WAIT", "DONE", "PRESS"} and target is None:
                raise ValueError(f"target is required for {operation_name}")
        except Exception as exc:
            return {
                "ok": False,
                "operation": operation_name,
                "resolved_target": None,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "verified": False,
                "attempts": 0,
                "detail": f"{type(exc).__name__}: {exc}",
            }

        desktop = get_desktop()
        anchor = _capture_anchor(desktop, target)
        attempts = 1
        retry_details: list[str] = []
        effective_retries = 0 if operation_name in {"WAIT", "DONE"} else retry_count

        def on_retry(retry_number: int, reason: Any, delay: float, result: Any) -> None:
            nonlocal attempts
            attempts = retry_number + 1
            retry_details.append(f"retry {retry_number} after {delay:.3f}s: {reason}")

        def invoke() -> tuple[bool, dict[str, Any]]:
            return _run_attempt(
                desktop,
                operation=operation_name,
                target=target,
                anchor=anchor,
                text=text,
                verify=verify_conditions,
                timeout=timeout_value,
                button=normalized_button,
                attempt_number=attempts,
            )

        try:
            outcome = retry_call(
                invoke,
                retries=effective_retries,
                base_delay=0.25,
                backoff=2.0,
                jitter=True,
                on_retry=on_retry,
            )
        except Exception as exc:
            outcome = (False, {"detail": f"{type(exc).__name__}: {exc}", "verified": False})

        ok, payload = outcome
        detail = str(payload.get("detail", "action completed"))
        if retry_details:
            detail = f"{detail}; {' | '.join(retry_details)}"
        return {
            "ok": bool(ok),
            "operation": operation_name,
            "resolved_target": payload.get("resolved_target"),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "verified": bool(payload.get("verified", False)),
            "attempts": attempts,
            "detail": detail,
        }
