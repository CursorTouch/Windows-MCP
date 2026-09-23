"""Typed action-space construction and model-choice validation.

The core algorithm is ported from browser-use/jev-ultrafast (MIT).  The public
``action_space`` helper accepts either the upstream action descriptors or the
structured element rows produced by ``WebAutomationService.snapshot_data``.
"""

from __future__ import annotations

import math
import re
from typing import Any

from windows_mcp.agent.questions import NEXT_ACTION, TARGET

Action = dict[str, Any]
TargetGroups = dict[str, dict[str, Action]]


def validate_choice(answer: Any, ids: Any) -> dict[str, Any]:
    """Validate one typed choice and return it unchanged.

    A valid answer must contain ``choice``, ``probabilities``, and
    ``confidence``.  The probability keys must exactly match the candidate
    ids, all numbers must be finite and in ``[0, 1]``, the distribution must
    sum to one within ``0.02``, and the selected choice must be maximal.
    """

    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(
                type(number) in (int, float)
                and math.isfinite(number)
                and 0 <= number <= 1
                for number in numbers
            )
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def _reference_action_space(actions: list[Action]) -> tuple[list[Action], TargetGroups, dict[str, Action]]:
    """Port of the upstream ``action_space`` implementation."""

    elements: list[Action] = []
    indices: dict[Any, str] = {}
    targets: TargetGroups = {}
    controls: dict[str, Action] = {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action.get("kind")
        if kind not in operations:
            action_id = action.get("id")
            if action_id is None:
                continue
            controls[str(action_id).upper()] = action
            continue
        node = action.get("node")
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {
                key: action[key]
                for key in ("role", "value", "checked", "selected", "expanded")
                if key in action
            }
            element.update(
                index=index,
                label=str(action.get("label", "")).split(" → ")[0],
                operations=[],
            )
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append(
                {
                    "index": target,
                    "label": action.get("label", ""),
                    "value": action.get("value"),
                }
            )
        group[target] = action
    return elements, targets, controls


def _is_text_element(element: Action) -> bool:
    """Return whether an observed element accepts direct text input."""

    role = str(element.get("role", "")).casefold()
    tag = str(element.get("tag", "")).casefold()
    if role in {"textbox", "searchbox", "spinbutton"}:
        return True
    if tag in {"textarea", "input"}:
        input_type = str(element.get("input_type", "")).casefold()
        return input_type not in {
            "button",
            "checkbox",
            "color",
            "file",
            "hidden",
            "image",
            "radio",
            "range",
            "reset",
            "submit",
        }
    return bool(element.get("contenteditable"))


def _structured_action_space(
    elements: list[Action],
) -> tuple[list[Action], TargetGroups, dict[str, Action]]:
    """Build indexed operations from ``snapshot_data`` element rows."""

    observed: list[Action] = []
    targets: TargetGroups = {}
    controls: dict[str, Action] = {}
    seen: set[int] = set()

    for position, row in enumerate(elements, start=1):
        try:
            ref = int(row.get("ref", position))
        except (TypeError, ValueError):
            continue
        if ref < 1 or ref in seen:
            continue
        seen.add(ref)
        if bool(row.get("disabled")):
            continue

        index = str(ref)
        label = str(row.get("label") or row.get("name") or "").strip()[:160]
        role = str(row.get("role") or "").strip().casefold()
        tag = str(row.get("tag") or "").strip().casefold()
        current_value = row.get("value")
        if current_value is None:
            current_value = ""
        operation_names: list[str] = []
        element: Action = {
            "index": index,
            "label": label,
            "role": role,
            "tag": tag,
            "value": current_value,
            "operations": operation_names,
        }
        for key in ("href", "checked", "selected", "expanded", "input_type"):
            if key in row:
                element[key] = row[key]

        if tag == "select":
            options = row.get("options") if isinstance(row.get("options"), list) else []
            option_rows: list[Action] = []
            for option_number, option in enumerate(options, start=1):
                if not isinstance(option, dict) or option.get("disabled"):
                    continue
                target = f"{index}:{option_number}"
                option_label = str(
                    option.get("label") or option.get("text") or option.get("value") or ""
                )[:160]
                option_value = option.get("value")
                candidate = {
                    "id": f"select:{target}",
                    "kind": "select",
                    "node": ref,
                    "ref": ref,
                    "label": f"{label} → {option_label}",
                    "role": role,
                    "value": option_value,
                    "current_value": current_value,
                }
                targets.setdefault("SELECT", {})[target] = candidate
                option_rows.append(
                    {"index": target, "label": option_label, "value": option_value}
                )
            if option_rows:
                operation_names.append("SELECT")
                element["options"] = option_rows
        else:
            candidate = {
                "id": f"click:{index}",
                "kind": "click",
                "node": ref,
                "ref": ref,
                "label": label,
                "role": role,
                "value": current_value,
            }
            targets.setdefault("CLICK", {})[index] = candidate
            operation_names.append("CLICK")

        if _is_text_element(row) and tag != "select":
            candidate = {
                "id": f"type:{index}",
                "kind": "fill",
                "node": ref,
                "ref": ref,
                "label": label,
                "role": role,
                "value": current_value,
                "current_value": current_value,
            }
            targets.setdefault("TYPE_TEXT", {})[index] = candidate
            operation_names.append("TYPE_TEXT")

        control_name = row.get("control_operation")
        if isinstance(control_name, str) and control_name.strip():
            normalized = control_name.strip().upper()
            controls[normalized] = {
                "id": normalized,
                "kind": "control",
                "node": ref,
                "ref": ref,
                "label": label or normalized,
            }

        if operation_names:
            observed.append(element)

    return observed, targets, controls


def action_space(actions: list[Action]) -> tuple[list[Action], TargetGroups, dict[str, Action]]:
    """Build element rows and operation-specific target choices.

    Upstream action descriptors use ``kind`` values ``click``/``fill``/``select``.
    Structured rows from ``snapshot_data`` use ``ref``/``role``/``tag`` and are
    converted into the same operation groups.
    """

    if not isinstance(actions, list):
        raise TypeError("actions must be a list")
    if any(isinstance(item, dict) and "kind" in item for item in actions):
        return _reference_action_space(actions)
    return _structured_action_space(actions)


_URL_RE = re.compile(r"https?://[^\s\"'<>)\\]+")


def extract_urls(goal: str) -> list[str]:
    """Return absolute URLs mentioned in the goal, de-duplicated, in order.

    Navigation targets come from the *goal text*, never from the model, so a
    policy can only select among URLs the user actually asked for. That keeps the
    "model output never becomes a selector or a URL" invariant intact while still
    letting a task jump straight to a page instead of hunting for a search box.
    """

    found: list[str] = []
    for match in _URL_RE.findall(goal or ""):
        url = match.rstrip(".,;:!?")
        if url not in found:
            found.append(url)
    return found


def build_questions(
    state: dict[str, Any], goal: str
) -> tuple[dict[str, Any], list[Action], TargetGroups, dict[str, Action]]:
    """Build the TypeSafe-style questions for the current observed state."""

    source = state.get("actions")
    if source is None:
        source = state.get("elements", [])
    elements, targets, controls = action_space(source)
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM supplies the value.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations: dict[str, str] = {
        operation: labels[operation] for operation in targets if operation in labels
    }
    operations.update({key: value.get("label", key) for key, value in controls.items()})
    operations.update(
        SCROLL_UP="Scroll the page up to reveal more content.",
        SCROLL_DOWN="Scroll the page down to reveal more content.",
        WAIT="Wait briefly because the needed control is absent, disabled, or loading.",
        DONE="Every requirement is visibly satisfied.",
        BLOCKED="No supported operation can progress.",
    )
    for position, url in enumerate(extract_urls(goal), start=1):
        operations[f"NAVIGATE_{position}"] = f"Navigate the browser directly to {url}"
    questions: dict[str, Any] = {
        "operation": {
            "type": "choice",
            "criteria": operations,
            "instructions": {"goal": goal, "rules": NEXT_ACTION},
        }
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {action.get('label', '')}",
                    "current_value": action.get(
                        "current_value", action.get("value", "")
                    ),
                    **{
                        key: action[key]
                        for key in ("role", "checked", "selected", "expanded")
                        if key in action
                    },
                }
                for index, action in candidates.items()
            },
            "instructions": {
                "goal": goal,
                "operation": operation,
                "rules": [NEXT_ACTION, TARGET],
            },
        }
    return questions, elements, targets, controls


def make_choice_answer(choice: str, ids: list[str], confidence: float) -> dict[str, Any]:
    """Create a valid one-hot typed choice for providers without probabilities."""

    if choice not in ids:
        raise ValueError("Model choice is not in the candidate set.")
    if type(confidence) not in (int, float) or not math.isfinite(confidence):
        raise ValueError("Model confidence must be a finite number.")
    if not 0 <= confidence <= 1:
        raise ValueError("Model confidence must be between 0 and 1.")
    probabilities = {candidate: 0.0 for candidate in ids}
    probabilities[choice] = 1.0
    return {"choice": choice, "probabilities": probabilities, "confidence": float(confidence)}


def decision_from_answers(
    answers: dict[str, Any], targets: TargetGroups, controls: dict[str, Action]
) -> dict[str, Any]:
    """Validate operation and selected target heads into one executable decision."""

    operations = {
        "SCROLL_UP",
        "SCROLL_DOWN",
        "WAIT",
        "DONE",
        "BLOCKED",
    }
    operations.update(targets)
    operations.update(controls)

    # NAVIGATE_* candidates are generated from the goal by build_questions,
    # not from the DOM, so they are absent from the two observation-derived
    # collections above. Recover their ids from the provider distribution;
    # validate_choice still enforces that the selected id is present and
    # maximal, while the executor separately enforces that its URL index is in
    # range for the goal.
    raw_operation = answers.get("operation", {})
    raw_probabilities = (
        raw_operation.get("probabilities", {})
        if isinstance(raw_operation, dict)
        else {}
    )
    if isinstance(raw_probabilities, dict):
        operations.update(
            candidate
            for candidate in raw_probabilities
            if isinstance(candidate, str)
            and re.fullmatch(r"NAVIGATE_[1-9]\d*", candidate)
        )

    operation_answer = validate_choice(answers.get("operation", {}), operations)
    operation = operation_answer["choice"]
    target: str | None = None
    target_answer: dict[str, Any] | None = None
    probabilities: dict[str, float] = {}

    if operation in targets:
        target_answer = validate_choice(
            answers.get(operation.lower() + "_target", {}), targets[operation]
        )
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {
            action["id"]: target_answer["probabilities"][index]
            for index, action in targets[operation].items()
        }
    elif operation in controls:
        choice = controls[operation]["id"]
        probabilities[choice] = operation_answer["probabilities"][operation]
    else:
        choice = operation
        probabilities[choice] = operation_answer["probabilities"][operation]

    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": answers,
    }


__all__ = [
    "action_space",
    "build_questions",
    "decision_from_answers",
    "make_choice_answer",
    "validate_choice",
]


