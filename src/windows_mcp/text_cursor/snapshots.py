"""Build serializable snapshots from UI Automation caret information."""

from __future__ import annotations

from typing import Any, Optional

from comtypes import COMError
from windows_mcp.uia import TextPatternRangeEndpoint, TextRange, TextUnit

from .constants import MAX_SELECTED_TEXT_CHARS
from .discovery import UIACaretInfo
from .errors import TextCursorError
from .models import CursorSnapshot, ScreenRect


def endpoint_offset(
    caret_info: UIACaretInfo,
    endpoint: TextPatternRangeEndpoint,
) -> Optional[int]:
    """Return the character offset from the document start to the given
    endpoint (Start or End) of the caret/selection range."""
    marker = caret_info.text_range.Clone()
    marker.Collapse(toEnd=(endpoint == TextPatternRangeEndpoint.End))

    try:
        return marker.GetStartOffset()
    except (COMError, AttributeError, TypeError):
        return None


def bounding_screen_rects(
    text_range: TextRange,
    try_again_if_err: bool = True,
) -> list[ScreenRect]:
    """Return the range's bounding boxes as `ScreenRect`s (one per visible line)."""
    try:
        rects = [
            ScreenRect(
                left=float(rect.left),
                top=float(rect.top),
                width=float(rect.width()),
                height=float(rect.height()),
            )
            for rect in text_range.GetBoundingRectangles()
        ]
        if len(rects) == 0 and text_range.IsDegenerate() and try_again_if_err:
            # A caret (degenerate range) sometimes has no bounding rectangle;
            # extend it by one character and try once more.
            adjacent = text_range.Clone()
            moved = adjacent.MoveEndpointByUnit(
                TextPatternRangeEndpoint.End, TextUnit.Character, 1, waitTime=0
            )
            if int(moved) != 0:
                return bounding_screen_rects(adjacent, False)  # avoid recursion
        return rects

    except (COMError, AttributeError, TypeError, ValueError):
        return []


def make_snapshot(
    caret_info: UIACaretInfo,
    context_chars: int,
    *,
    include_context: bool = True,
    include_selected_text: bool = True,
) -> CursorSnapshot:
    start = endpoint_offset(caret_info, TextPatternRangeEndpoint.Start)
    # A caret is a degenerate range: both endpoints resolve to the same offset,
    # and only caret_offset_units (== start) is emitted. Computing the End
    # endpoint would be a second full Move(-MAX) walk back to DocumentRange
    # start for nothing, so reuse start. Only a real selection needs End.
    # (The start-is-None short-circuit keeps the unavailable-offset error below
    # from being masked by inspecting the range first.)
    if start is not None and caret_info.exact_caret:
        end = start
    else:
        end = endpoint_offset(caret_info, TextPatternRangeEndpoint.End)

    if start is None or end is None:
        raise TextCursorError(
            "The provider did not allow calculating UIA TextUnit_Character offsets."
        )

    warnings: list[str] = []

    selected_text = None
    selected_text_truncated = False
    if include_selected_text and not caret_info.exact_caret:
        # Read one extra character so a selection sitting exactly on the limit
        # is not mistaken for a truncated one.
        try:
            selected_text = caret_info.text_range.GetText(MAX_SELECTED_TEXT_CHARS + 1)
        except COMError:
            warnings.append(
                "The provider did not allow reading selected_text. The field was omitted; "
                "selection_start_units and selection_end_units are still available."
            )
        else:
            if len(selected_text) > MAX_SELECTED_TEXT_CHARS:
                selected_text = selected_text[:MAX_SELECTED_TEXT_CHARS] + "…"
                selected_text_truncated = True

    # --- Read the text on both sides of the caret/selection. ---
    before = None
    after = None

    if include_context:
        try:
            before = caret_info.text_range.GetTextBefore(context_chars)
        except COMError:
            warnings.append(
                "The provider did not allow reading text_before. The field was omitted."
            )

        try:
            after = caret_info.text_range.GetTextAfter(context_chars)
        except COMError:
            warnings.append("The provider did not allow reading text_after. The field was omitted.")

    if caret_info.selection_count > 1:
        warnings.append(
            f"TextPattern.GetSelection returned {caret_info.selection_count} "
            "disjoint selections. TextCursor reports and uses only the first "
            "selection; the remaining selections are ignored."
        )

    if not caret_info.exact_caret:
        warnings.append(
            "TextPattern.GetSelection returned a non-empty selection. "
            "The active caret endpoint is unknown."
        )

    if selected_text_truncated:
        warnings.append(
            f"selected_text was truncated to {MAX_SELECTED_TEXT_CHARS} characters "
            "(marked with a trailing ellipsis). The selection_start_units and "
            "selection_end_units offsets still describe the full selection."
        )

    try:
        element_name = caret_info.element.Name or None
    except COMError:
        element_name = None

    return CursorSnapshot(
        provider=caret_info.source,
        element_name=element_name,
        type="caret" if caret_info.exact_caret else "range",
        caret_offset_units=start if caret_info.exact_caret else None,  # caret only
        selection_start_units=(start if not caret_info.exact_caret else None),  # range only
        selection_end_units=end if not caret_info.exact_caret else None,  # range only
        selected_text=selected_text,
        text_before=before,
        text_after=after,
        bounding_rects=bounding_screen_rects(caret_info.text_range),
        warnings=warnings,
    )


def snapshot_position(snapshot: CursorSnapshot) -> dict[str, Any]:
    """Return only the real caret or selection coordinates from a snapshot."""
    if snapshot.type == "caret":
        return {
            "type": "caret",
            "caret_offset_units": snapshot.caret_offset_units,
        }

    return {
        "type": "range",
        "selection_start_units": snapshot.selection_start_units,
        "selection_end_units": snapshot.selection_end_units,
    }
