"""Low-level wrappers and helpers for Windows UI Automation text ranges."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Optional

import comtypes.client
from comtypes import COMError

from .constants import (
    CLSID_CUIAUTOMATION,
    CLSID_CUIAUTOMATION8,
    MAX_TEXT_UNIT_MOVE,
)
from .models import ScreenRect


class UIAModule:
    def __init__(self, raw_module):
        self.raw_module = raw_module


class UIAAutomationObject:
    def __init__(self, raw_obj):
        self.raw_obj = raw_obj

    def get_focused_element(self) -> UIAElement | None:
        raw_elem = self.raw_obj.GetFocusedElement()
        if not raw_elem:
            return None
        return UIAElement(raw_elem, self.raw_obj)


def create_automation() -> tuple[UIAModule, UIAAutomationObject]:
    """
    Load UIAutomationCore.dll type information and create the UIA client.
    CUIAutomation8 is preferred; CUIAutomation is used as a compatibility fallback.
    """
    raw_uia = comtypes.client.GetModule("UIAutomationCore.dll")

    try:
        automation = comtypes.client.CreateObject(
            CLSID_CUIAUTOMATION8,
            interface=raw_uia.IUIAutomation,
        )
    except COMError:
        automation = comtypes.client.CreateObject(
            CLSID_CUIAUTOMATION,
            interface=raw_uia.IUIAutomation,
        )

    return UIAModule(raw_uia), UIAAutomationObject(automation)


class TextRangeEndpoint(enum.Enum):
    """Mirrors the UIA TextPatternRangeEndpoint enumeration."""

    Start = 0  # TextPatternRangeEndpoint_Start
    End = 1  # TextPatternRangeEndpoint_End


class TextUnit(enum.Enum):
    """Mirrors the UIA TextUnit enumeration."""

    Character = 0  # TextUnit_Character
    Format = 1  # TextUnit_Format
    Word = 2  # TextUnit_Word
    Line = 3  # TextUnit_Line
    Paragraph = 4  # TextUnit_Paragraph
    Page = 5  # TextUnit_Page
    Document = 6  # TextUnit_Document


class UIAElement:
    """A simple wrapper for UIA Element"""

    def __init__(self, raw_element: Any, raw_uia_object: Any):
        self.raw_element = raw_element
        self.raw_uia_obj = raw_uia_object

    def get_pattern(self, pattern_id: int, interface: Any) -> TextPattern | None:
        """Return the requested control pattern wrapped as a TextPattern, or None."""
        try:
            unknown = self.raw_element.GetCurrentPattern(pattern_id)
            if not unknown:
                return None

            raw_pattern = unknown.QueryInterface(interface)
            if not raw_pattern:
                return None
            return TextPattern(raw_pattern)

        except (COMError, AttributeError, TypeError):
            return None

    def get_name(self) -> str:
        try:
            return str(self.raw_element.CurrentName or "")
        except (COMError, AttributeError):
            return ""

    def parent(self) -> UIAElement | None:
        walker = self.raw_uia_obj.RawViewWalker
        try:
            ret = walker.GetParentElement(self.raw_element)
        except COMError:
            ret = None

        if not ret:
            return None

        return UIAElement(ret, self.raw_uia_obj)

    def set_focus(self):
        self.raw_element.SetFocus()


class TextPattern:
    """A simple wrapper for UIA TextPattern"""

    def __init__(self, raw_pattern):
        self.raw_pattern = raw_pattern

    def get_selections(self) -> list[TextRange]:
        try:
            selections = self.raw_pattern.GetSelection()

            if not selections:
                return []

            if int(selections.Length) <= 0:
                return []

            return [
                TextRange(selections.GetElement(index)) for index in range(int(selections.Length))
            ]

        except (COMError, AttributeError, TypeError, ValueError):
            return []

    def get_first_selection(self) -> TextRange | None:
        selections = self.get_selections()
        if not selections:
            return None

        return selections[0]

    def document_range(self) -> TextRange:
        return TextRange(self.raw_pattern.DocumentRange)


class TextRange:
    """A simple wrapper for UIA TextRange"""

    def __init__(self, raw_range):
        self.raw_range = raw_range

    def clone(self) -> TextRange:
        return TextRange(self.raw_range.Clone())

    def move(self, unit: TextUnit, count: int) -> int:
        """Moves the text range the specified number of TextUnit units within the document range.
        Return the number of units actually moved
        """
        return int(self.raw_range.Move(unit.value, count))

    def select(self):
        return self.raw_range.Select()

    def move_endpoint_by_range(
        self,
        src_endpoint: TextRangeEndpoint,
        other: TextRange,
        target_endpoint: TextRangeEndpoint,
    ):
        """Moves one endpoint of the current text range to the specified endpoint of a second text range."""
        self.raw_range.MoveEndpointByRange(
            src_endpoint.value,
            other.raw_range,
            target_endpoint.value,
        )

    def move_endpoint_by_unit(self, endpoint: TextRangeEndpoint, unit: TextUnit, count: int) -> int:
        """Moves one endpoint of the text range the specified number of TextUnit units within the document range.
        Return the number of units actually moved
        """
        return int(
            self.raw_range.MoveEndpointByUnit(
                endpoint.value,
                unit.value,
                count,
            )
        )

    def compare_endpoints(
        self,
        src_endpoint: TextRangeEndpoint,
        other: TextRange,
        target_endpoint: TextRangeEndpoint,
    ) -> int:
        return int(
            self.raw_range.CompareEndpoints(
                src_endpoint.value,
                other.raw_range,
                target_endpoint.value,
            )
        )

    def is_degenerate(self) -> bool:
        comparison = self.compare_endpoints(
            TextRangeEndpoint.Start,
            self,
            TextRangeEndpoint.End,
        )

        return int(comparison) == 0

    def collapse_range(self, *, to_end: bool) -> None:
        """Collapse the range to a single point, clearing any selection.

        to_end=False collapses to the start (left) endpoint; to_end=True
        collapses to the end (right) endpoint.
        """
        if to_end:
            # Start --move-> End.
            self.move_endpoint_by_range(
                TextRangeEndpoint.Start,
                self,
                TextRangeEndpoint.End,
            )
        else:
            # End --move-> Start.
            self.move_endpoint_by_range(
                TextRangeEndpoint.End,
                self,
                TextRangeEndpoint.Start,
            )

    def get_text(self, max_length: int = -1) -> str:
        return str(self.raw_range.GetText(max_length) or "")

    def text_before(self, count: int) -> str:
        """Return up to `count` characters immediately before the range."""
        clone = self.clone()

        # Collapse to the start of the range.
        clone.collapse_range(to_end=False)

        # Extend the start endpoint backward.
        clone.move_endpoint_by_unit(TextRangeEndpoint.Start, TextUnit.Character, -count)

        return clone.get_text()

    def text_after(self, count: int) -> str:
        """Return up to `count` characters immediately after the range."""
        clone = self.clone()

        # Collapse to the end of the range.
        clone.collapse_range(to_end=True)

        # Extend the end endpoint forward.
        clone.move_endpoint_by_unit(TextRangeEndpoint.End, TextUnit.Character, count)

        return clone.get_text()

    def bounding_rectangles(self, try_again_if_err: bool = True) -> list[ScreenRect]:
        try:
            # An array of bounding rectangles for each fully or partially
            # visible line of text in the range. A selection can span multiple
            # lines, so each line is returned as its own rectangle. The result
            # is a flat tuple of (left, top, width, height) per rectangle:
            # (x0, y0, w0, h0, x1, y1, w1, h1, ...).
            values = self.raw_range.GetBoundingRectangles()  # type: tuple[float, ...]
            if values is None:
                return []

            flat = list(values)

            rects = [
                ScreenRect(
                    left=float(flat[index]),
                    top=float(flat[index + 1]),
                    width=float(flat[index + 2]),
                    height=float(flat[index + 3]),
                )
                for index in range(0, len(flat) - 3, 4)
            ]
            if len(rects) == 0 and self.is_degenerate() and try_again_if_err:
                # A caret (degenerate range) sometimes has no bounding
                # rectangle; extend it by one character and try once more.
                adjacent = self.clone()
                moved = adjacent.move_endpoint_by_unit(TextRangeEndpoint.End, TextUnit.Character, 1)

                if int(moved) != 0:
                    return adjacent.bounding_rectangles(False)  # avoid recursion
            return rects

        except (
            COMError,
            AttributeError,
            TypeError,
            ValueError,
        ):
            return []

    # A TextRange has no stable value-based hash (its endpoints can move) and
    # equality requires live COM calls, so keep instances explicitly
    # unhashable. Python already does this once __eq__ is defined; stating it
    # makes the intent obvious and guards against accidental set/dict use.
    __hash__ = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TextRange):
            return NotImplemented

        try:
            start_comparison = self.compare_endpoints(
                TextRangeEndpoint.Start,
                other,
                TextRangeEndpoint.Start,
            )

            end_comparison = self.compare_endpoints(
                TextRangeEndpoint.End,
                other,
                TextRangeEndpoint.End,
            )
        except COMError:
            # A stale range can no longer be compared; treat it as not equal
            # rather than propagating the COM failure out of an equality check.
            return False

        return int(start_comparison) == 0 and int(end_comparison) == 0


@dataclass
class UIACaretInfo:
    element: UIAElement
    text_pattern: TextPattern
    text_range: TextRange
    source: str
    exact_caret: bool
    selection_count: int = 1


def range_start_offset(
    text_range: TextRange,
) -> Optional[int]:
    """Return the range start in UIA TextUnit_Character steps.

    The offset is measured from DocumentRange start and uses the same
    provider-defined coordinate system as absolute move/select actions.

    Cost note: UIA exposes no direct "character index" query, so the offset is
    derived by moving a degenerate range back to DocumentRange start. Providers
    that implement Move as a linear walk make this O(offset), i.e. proportional
    to the distance from the document start. This is negligible for typical text
    controls but can be noticeable in very large documents, so avoid high-
    frequency polling there.
    """
    try:
        clone = text_range.clone()
        clone.collapse_range(to_end=False)
        # Walk back to the very start of the document.
        moved = clone.move(TextUnit.Character, -MAX_TEXT_UNIT_MOVE)
        # Moving backward returns a negative count, so negate it to get the
        # positive offset from DocumentRange start.
        return -moved

    except (COMError, AttributeError, TypeError):
        return None
