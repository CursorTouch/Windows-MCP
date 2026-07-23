"""Implement TextCursor write modes against UI Automation ranges."""

from __future__ import annotations

from typing import Any, NamedTuple, Union

from .models import (
    CollapseSelectionAction,
    MoveAbsoluteAction,
    MoveRelativeAction,
    SelectAbsoluteAction,
    SelectAllAction,
    SelectRelativeAction,
)
from .ranges import (
    apply_change,
    document_position,
    get_origin_from_range,
    make_range,
    verify,
)
from .uia import UIACaretInfo, TextUnit


class WriteActionResult(NamedTuple):
    verified: bool | None  # None means there is no verification after action
    target_info: dict[str, Any]


def apply_move_relative(action: MoveRelativeAction, caret_info: UIACaretInfo) -> WriteActionResult:
    # For a selection, resolve which endpoint the move is relative to.
    target = get_origin_from_range(caret_info, action.origin)
    target_delta = target.move(TextUnit.Character, action.delta)
    apply_change(caret_info, target)
    verified = verify(caret_info, target, action.verify)
    return WriteActionResult(verified, {"target_delta": target_delta})


def apply_move_absolute(action: MoveAbsoluteAction, caret_info: UIACaretInfo) -> WriteActionResult:
    target, target_offset = document_position(caret_info, action.offset)
    apply_change(caret_info, target)
    verified = verify(caret_info, target, action.verify)
    return WriteActionResult(verified, {"target_offset_units": target_offset})


def apply_select_relative(
    action: SelectRelativeAction, caret_info: UIACaretInfo
) -> WriteActionResult:
    origin = get_origin_from_range(caret_info, action.origin)

    start_marker = origin.clone()
    end_marker = origin.clone()

    target_start_delta = start_marker.move(TextUnit.Character, action.start_delta)
    target_end_delta = end_marker.move(TextUnit.Character, action.end_delta)

    target = make_range(start_marker, end_marker)

    apply_change(caret_info, target)
    verified = verify(caret_info, target, action.verify)

    return WriteActionResult(
        verified,
        {
            "target_start_delta": target_start_delta,
            "target_end_delta": target_end_delta,
        },
    )


def apply_select_absolute(
    action: SelectAbsoluteAction,
    caret_info: UIACaretInfo,
) -> WriteActionResult:
    start_marker, target_start = document_position(caret_info, action.start)
    end_marker, target_end = document_position(caret_info, action.end)

    target = make_range(start_marker, end_marker)

    apply_change(caret_info, target)
    verified = verify(caret_info, target, action.verify)

    return WriteActionResult(
        verified,
        {
            "target_start_units": target_start,
            "target_end_units": target_end,
        },
    )


def apply_select_all(
    action: SelectAllAction,
    caret_info: UIACaretInfo,
) -> WriteActionResult:
    target = caret_info.text_pattern.document_range()
    apply_change(caret_info, target)
    verified = verify(caret_info, target, action.verify)
    return WriteActionResult(verified, {})


def apply_collapse_selection(
    action: CollapseSelectionAction,
    caret_info: UIACaretInfo,
) -> WriteActionResult:
    target = caret_info.text_range.clone()
    target.collapse_range(to_end=(action.edge == "end"))

    apply_change(caret_info, target)
    verified = verify(caret_info, target, action.verify)
    return WriteActionResult(verified, {"edge": action.edge})


WriteAction = Union[
    MoveRelativeAction,
    MoveAbsoluteAction,
    SelectRelativeAction,
    SelectAbsoluteAction,
    SelectAllAction,
    CollapseSelectionAction,
]


def apply_write(action: WriteAction, caret_info: UIACaretInfo) -> WriteActionResult:
    match action:
        case MoveRelativeAction():
            return apply_move_relative(action, caret_info)
        case MoveAbsoluteAction():
            return apply_move_absolute(action, caret_info)
        case SelectRelativeAction():
            return apply_select_relative(action, caret_info)
        case SelectAbsoluteAction():
            return apply_select_absolute(action, caret_info)
        case SelectAllAction():
            return apply_select_all(action, caret_info)
        case CollapseSelectionAction():
            return apply_collapse_selection(action, caret_info)
        case _:
            raise TypeError(f"Unsupported action type: {type(action)!r}")
