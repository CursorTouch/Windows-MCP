"""Implement TextCursor write modes against UI Automation ranges."""

from __future__ import annotations

from typing import Any, NamedTuple, Union

from windows_mcp.uia import TextUnit

from .discovery import UIACaretInfo
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


class WriteActionResult(NamedTuple):
    verified: bool | None  # None means there is no verification after action
    target_info: dict[str, Any]


def apply_move_relative(action: MoveRelativeAction, caret_info: UIACaretInfo) -> WriteActionResult:
    # For a selection, resolve which endpoint the move is relative to.
    target = get_origin_from_range(caret_info, action.origin)
    target_delta = target.Move(TextUnit.Character, action.delta, waitTime=0)
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

    start_marker = origin.Clone()
    end_marker = origin.Clone()

    target_start_delta = start_marker.Move(TextUnit.Character, action.start_delta, waitTime=0)
    target_end_delta = end_marker.Move(TextUnit.Character, action.end_delta, waitTime=0)

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
    target = caret_info.text_pattern.DocumentRange
    apply_change(caret_info, target)
    verified = verify(caret_info, target, action.verify)
    return WriteActionResult(verified, {})


def apply_collapse_selection(
    action: CollapseSelectionAction,
    caret_info: UIACaretInfo,
) -> WriteActionResult:
    target = caret_info.text_range.Clone()
    target.Collapse(toEnd=(action.edge == "end"))

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
