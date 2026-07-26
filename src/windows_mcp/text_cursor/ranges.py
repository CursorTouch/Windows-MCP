"""Construct and apply UI Automation text ranges."""

from comtypes import COMError
from windows_mcp.uia import TextPatternRangeEndpoint, TextRange, TextUnit

from .discovery import UIACaretInfo
from .errors import TextCursorError
from .models import RelativeOrigin


def get_origin_from_range(
    caret_info: UIACaretInfo,
    origin: RelativeOrigin,
) -> TextRange:
    """Return the base position a move/select operation is measured from.

    A range has two endpoints, so `origin` selects which one to start from:
    'caret' (only valid for a degenerate range), 'selection_start', or
    'selection_end'.
    """
    base = caret_info.text_range.Clone()

    if origin == "caret":
        if not base.IsDegenerate():
            raise RuntimeError(
                "The current range is a non-empty selection. "
                "The TextPattern fallback does not reveal which endpoint "
                "is the active caret. Use selection_start or selection_end."
            )

        return base

    base.Collapse(toEnd=(origin == "selection_end"))
    return base


def document_position(
    caret_info: UIACaretInfo,
    offset: int,
) -> tuple[TextRange, int]:
    """Build a degenerate range at `offset` characters from the document start."""
    # Get the range spanning the whole document.
    target = caret_info.text_pattern.DocumentRange
    # Collapse to its start endpoint.
    target.Collapse(toEnd=False)
    actual_moved = int(target.Move(TextUnit.Character, offset, waitTime=0))
    return target, actual_moved


def make_range(
    start_marker: TextRange,
    end_marker: TextRange,
) -> TextRange:
    # s----------e
    # ^
    target = start_marker.Clone()
    target.Collapse(toEnd=False)

    # s----------e
    # ^----------^
    target.MoveEndpointByRange(
        TextPatternRangeEndpoint.End,
        end_marker,
        TextPatternRangeEndpoint.Start,
        waitTime=0,
    )

    # Check whether target's start endpoint has passed its end endpoint (> 0).
    comparison = target.CompareEndpoints(
        TextPatternRangeEndpoint.Start,
        target,
        TextPatternRangeEndpoint.End,
    )

    if int(comparison) > 0:
        raise RuntimeError("The calculated selection start is after its end.")
    return target


def apply_change(caret_info: UIACaretInfo, target: TextRange) -> None:
    if not caret_info.element.SetFocus():
        raise TextCursorError("Unable to focus the target text control.")

    # Move() only modifies the local range.
    # Select() requests the actual caret/selection change.
    if not target.Select(waitTime=0):
        raise TextCursorError("The provider did not accept the requested caret/selection change.")


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

    try:
        actual = caret_info.text_pattern.GetFirstSelection()
        if actual is None:
            return False

        # Compare() is True only when both ranges share the same endpoints.
        return target.Compare(actual)
    except COMError:
        # A stale range can no longer be compared; treat it as a mismatch
        # rather than propagating the COM failure out of verification.
        return False
