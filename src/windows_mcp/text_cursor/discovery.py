"""Locate the focused UIA element that exposes the caret/selection TextPattern.

This sits above the pure UIA wrappers in `uia` because it depends on the COM
worker thread (`worker.get_thread_automation`). Keeping it here lets `uia` stay
a leaf module that `worker` can import without forming a cycle.
"""

from __future__ import annotations

from typing import Optional

from comtypes import COMError

from .constants import UIA_TEXT_PATTERN_ID
from .uia import UIACaretInfo, UIAElement, UIAModule
from .worker import get_thread_automation


def try_get_caret_on_element(uia: UIAModule, element: UIAElement) -> Optional[UIACaretInfo]:
    # Use TextPattern.GetSelection rather than TextPattern2.GetCaretRange:
    # GetCaretRange does not expose the actual selection range, so handling it
    # separately is not worth the effort. The first selection is kept as-is:
    # a degenerate range is treated as a caret (exact_caret=True), and a
    # non-empty one as a range whose active caret endpoint is unknown.
    text_pattern = element.get_pattern(
        UIA_TEXT_PATTERN_ID,
        uia.raw_module.IUIAutomationTextPattern,
    )

    if text_pattern is not None:
        selections = text_pattern.get_selections()

        if selections:
            selection = selections[0]
            return UIACaretInfo(
                element=element,
                text_pattern=text_pattern,
                text_range=selection,
                source="TextPattern.GetSelection",
                exact_caret=selection.is_degenerate(),
                selection_count=len(selections),
            )

    return None


def find_caret_provider(max_parent_levels: int = 8) -> UIACaretInfo:
    """
    Start at the focused UIA element and walk up RawView parents.
    Some controls expose TextPattern on an ancestor rather than on the exact
    focused child.
    """
    uia, automation = get_thread_automation()

    element = automation.get_focused_element()

    if not element:
        raise RuntimeError("UI Automation returned no focused element.")

    element_name = element.get_name()

    tried_cnt = 0
    while element and tried_cnt < max_parent_levels + 1:
        tried_cnt += 1
        result = try_get_caret_on_element(uia, element)

        if result is not None:
            return result

        try:
            element = element.parent()
        except COMError:
            element = None

    raise RuntimeError(
        f"The focused element {f'"{element_name}" ' if element_name else ''}and "
        f"its inspected parents do not expose TextPattern."
    )
