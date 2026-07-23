"""Construct and apply UI Automation text ranges."""

from .models import RelativeOrigin
from .uia import UIACaretInfo, TextRange, TextRangeEndpoint, TextUnit


def get_origin_from_range(
    caret_info: UIACaretInfo,
    origin: RelativeOrigin,
) -> TextRange:
    """Return the base position a move/select operation is measured from.

    A range has two endpoints, so `origin` selects which one to start from:
    'caret' (only valid for a degenerate range), 'selection_start', or
    'selection_end'.
    """
    base = caret_info.text_range.clone()

    if origin == "caret":
        if not base.is_degenerate():
            raise RuntimeError(
                "The current range is a non-empty selection. "
                "The TextPattern fallback does not reveal which endpoint "
                "is the active caret. Use selection_start or selection_end."
            )

        return base

    base.collapse_range(to_end=(origin == "selection_end"))
    return base


def document_position(
    caret_info: UIACaretInfo,
    offset: int,
) -> tuple[TextRange, int]:
    """Build a degenerate range at `offset` characters from the document start."""
    # Get the range spanning the whole document.
    target = caret_info.text_pattern.document_range()
    # Collapse to its start endpoint.
    target.collapse_range(to_end=False)
    actual_moved = int(target.move(TextUnit.Character, offset))
    return target, actual_moved


def make_range(
    start_marker: TextRange,
    end_marker: TextRange,
) -> TextRange:
    # s----------e
    # ^
    target = start_marker.clone()
    target.collapse_range(to_end=False)

    # s----------e
    # ^----------^
    target.move_endpoint_by_range(
        TextRangeEndpoint.End,
        end_marker,
        TextRangeEndpoint.Start,
    )

    # Check whether target's start endpoint has passed its end endpoint (> 0).
    comparison = target.compare_endpoints(
        TextRangeEndpoint.Start,
        target,
        TextRangeEndpoint.End,
    )

    if int(comparison) > 0:
        raise RuntimeError("The calculated selection start is after its end.")
    return target


def apply_change(caret_info: UIACaretInfo, target: TextRange):
    caret_info.element.set_focus()
    # Move() only modifies the local range.
    # Select() requests the actual caret/selection change.
    target.select()


def verify(
    caret_info: UIACaretInfo,
    target: TextRange,
    need_verify: bool,
) -> bool | None:
    """Verify the applied range matches `target` by reading the selection back.

    Returns None when verification was not requested.
    """
    if not need_verify:
        return None

    actual = caret_info.text_pattern.get_first_selection()
    if actual is None:
        return False

    return target == actual
