"""Locate the focused UIA control that exposes the caret/selection TextPattern.

Runs on the server's main-thread STA and reuses the shared `windows_mcp.uia`
client, so it can use `GetFocusedControl` and `Control` navigation directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from comtypes import COMError
from windows_mcp.uia import Control, GetFocusedControl, PatternId, TextPattern, TextRange


@dataclass
class UIACaretInfo:
    """UIA provider and text range that describe the current caret or selection."""

    element: Control
    text_pattern: TextPattern
    text_range: TextRange
    source: str
    exact_caret: bool
    selection_count: int = 1


def try_get_caret_on_element(control: Control) -> UIACaretInfo | None:
    # Use TextPattern.GetSelection rather than TextPattern2.GetCaretRange:
    # GetCaretRange does not expose the actual selection range, so handling it
    # separately is not worth the effort. The first selection is kept as-is:
    # a degenerate range is treated as a caret (exact_caret=True), and a
    # non-empty one as a range whose active caret endpoint is unknown.
    text_pattern = control.GetPattern(PatternId.TextPattern)

    if text_pattern is not None:
        try:
            selections = text_pattern.GetSelection()
        except (COMError, AttributeError, TypeError, ValueError):
            selections = []

        if selections:
            selection = selections[0]
            return UIACaretInfo(
                element=control,
                text_pattern=text_pattern,
                text_range=selection,
                source="TextPattern.GetSelection",
                exact_caret=selection.IsDegenerate(),
                selection_count=len(selections),
            )

    return None


def find_caret_provider(max_parent_levels: int = 8) -> UIACaretInfo:
    """
    Start at the focused UIA control and walk up RawView parents.
    Some controls expose TextPattern on an ancestor rather than on the exact
    focused child.
    """
    control = GetFocusedControl()

    if control is None:
        raise RuntimeError("UI Automation returned no focused element.")

    try:
        element_name = control.Name
    except COMError:
        element_name = ""

    tried_cnt = 0
    while control is not None and tried_cnt < max_parent_levels + 1:
        tried_cnt += 1
        result = try_get_caret_on_element(control)

        if result is not None:
            return result

        try:
            control = control.GetParentControl()
        except COMError:
            control = None

    raise RuntimeError(
        f"The focused element {f'"{element_name}" ' if element_name else ''}and "
        f"its inspected parents do not expose TextPattern."
    )
