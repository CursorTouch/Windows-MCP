#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Inspect and manipulate the caret/selection of the currently focused Windows
text control through UI Automation.

The caret/selection is discovered via IUIAutomationTextPattern.GetSelection()
on the focused element, walking up to its ancestors when needed.
IUIAutomationTextPattern2.GetCaretRange() is intentionally not used; see
try_get_caret_on_element for the rationale.

Supported modes:
- get_info
- move_relative
- move_absolute
- select_relative
- select_absolute
- select_all
- collapse_selection

Important:
- IUIAutomationTextRange.Move() only moves a client-side range.
- Select() asks the provider to apply that range as the real caret/selection.
- Write operations verify the applied range by reading it back.
- Some providers expose TextPattern but do not support moving the real caret.
"""

from __future__ import annotations

import asyncio
import ctypes
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Optional, Union, NamedTuple
import enum

import comtypes.client
from comtypes import COMError
from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# UI Automation constants
# ---------------------------------------------------------------------------

UIA_TEXT_PATTERN_ID = 10014
UIA_TEXT_PATTERN2_ID = 10024

CLSID_CUIAUTOMATION8 = "{E22AD333-B25F-460C-83D0-0581107395C9}"
CLSID_CUIAUTOMATION = "{FF48DBA4-60EF-4201-AA87-54103EEF594E}"

MAX_TEXT_UNIT_MOVE = 2_147_483_647

# Upper bound on the selected-text string embedded in a snapshot. Unlike the
# surrounding context (capped by context_chars), the selection can be the whole
# document (e.g. after select_all), so cap it here to keep the MCP payload small.
MAX_SELECTED_TEXT_CHARS = 4096


class TextCursorError(RuntimeError):
    """Base error raised by TextCursor operations."""


class TextCursorVerificationError(TextCursorError):
    """Raised when a TextCursor write cannot be verified."""


# ---------------------------------------------------------------------------
# Internal UIA result
# ---------------------------------------------------------------------------


@dataclass
class UIACaretInfo:
    element: UIAElement
    text_pattern: TextPattern
    text_range: TextRange
    source: str
    exact_caret: bool
    selection_count: int = 1


# ---------------------------------------------------------------------------
# UI Automation low-level helpers
# ---------------------------------------------------------------------------


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


# region
# ---------------------------------------------------------------------------
# MCP input models
# ---------------------------------------------------------------------------

RelativeOrigin = Literal[
    "caret",
    "selection_start",
    "selection_end",
]

CollapseEdge = Literal["start", "end"]


class ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay: float = Field(
        default=0.0,
        ge=0.0,
        le=300.0,
        description=(
            "Seconds to wait before locating the focused UIA element and "
            "executing this action. Use this to leave time for the user or "
            "another automation step to focus the target text control."
        ),
    )

    context_chars: int = Field(
        default=40,
        ge=0,
        le=4096,
        description=("Number of UIA character units to read around the caret or selection."),
    )

    verify: bool = Field(
        default=True,
        description=(
            "Read the selection back after a write operation and verify that "
            "the provider actually applied it."
        ),
    )


class GetInfoAction(ActionBase):
    mode: Literal["get_info"]


class MoveRelativeAction(ActionBase):
    mode: Literal["move_relative"]

    delta: int = Field(
        ge=-MAX_TEXT_UNIT_MOVE,
        le=MAX_TEXT_UNIT_MOVE,
        description=(
            "Signed UIA TextUnit_Character movement. Positive moves forward; "
            "negative moves backward."
        ),
    )

    origin: RelativeOrigin = Field(
        default="caret",
        description=(
            "Movement origin. For a non-empty TextPattern fallback selection, "
            "use selection_start or selection_end."
        ),
    )


class MoveAbsoluteAction(ActionBase):
    mode: Literal["move_absolute"]

    offset: int = Field(
        ge=0,
        le=MAX_TEXT_UNIT_MOVE,
        description="Target position in UIA TextUnit_Character steps from DocumentRange start.",
    )


class SelectRelativeAction(ActionBase):
    mode: Literal["select_relative"]

    origin: RelativeOrigin = "caret"

    start_delta: int = Field(
        ge=-MAX_TEXT_UNIT_MOVE,
        le=MAX_TEXT_UNIT_MOVE,
        description="Signed delta from origin to the inclusive selection start.",
    )
    end_delta: int = Field(
        ge=-MAX_TEXT_UNIT_MOVE,
        le=MAX_TEXT_UNIT_MOVE,
        description="Signed delta from origin to the exclusive selection end.",
    )

    @model_validator(mode="after")
    def validate_deltas(self) -> "SelectRelativeAction":
        if self.start_delta > self.end_delta:
            raise ValueError("start_delta must be less than or equal to end_delta")

        return self


class SelectAbsoluteAction(ActionBase):
    mode: Literal["select_absolute"]

    start: int = Field(
        ge=0,
        le=MAX_TEXT_UNIT_MOVE,
        description=(
            "Inclusive selection start in UIA TextUnit_Character steps from DocumentRange start."
        ),
    )

    end: int = Field(
        ge=0,
        le=MAX_TEXT_UNIT_MOVE,
        description=(
            "Exclusive selection end in UIA TextUnit_Character steps from DocumentRange start."
        ),
    )

    @model_validator(mode="after")
    def validate_offsets(self) -> "SelectAbsoluteAction":
        if self.start > self.end:
            raise ValueError("start must be less than or equal to end")

        return self


class SelectAllAction(ActionBase):
    mode: Literal["select_all"]


class CollapseSelectionAction(ActionBase):
    mode: Literal["collapse_selection"]
    edge: CollapseEdge


CursorAction = Annotated[
    Union[
        GetInfoAction,
        MoveRelativeAction,
        MoveAbsoluteAction,
        SelectRelativeAction,
        SelectAbsoluteAction,
        SelectAllAction,
        CollapseSelectionAction,
    ],
    Field(discriminator="mode"),
]


# endregion

# region
# ---------------------------------------------------------------------------
# MCP output models
# ---------------------------------------------------------------------------


class ScreenRect(BaseModel):
    left: float
    top: float
    width: float
    height: float


def _ignore_none(value: object) -> bool:
    return value is None


class CursorSnapshot(BaseModel):
    provider: str
    element_name: str | None = None

    type: Literal["caret", "range"]

    caret_offset_units: int | None = Field(
        default=None,
        exclude_if=_ignore_none,
        description=(
            "Caret offset in provider-defined UIA TextUnit_Character steps "
            "from DocumentRange start."
        ),
    )
    selection_start_units: int | None = Field(
        default=None,
        exclude_if=_ignore_none,
        description=(
            "Selection start in provider-defined UIA TextUnit_Character steps "
            "from DocumentRange start."
        ),
    )
    selection_end_units: int | None = Field(
        default=None,
        exclude_if=_ignore_none,
        description=(
            "Selection end in provider-defined UIA TextUnit_Character steps "
            "from DocumentRange start."
        ),
    )

    selected_text: str | None = Field(default=None, exclude_if=_ignore_none)
    text_before: str | None = Field(default=None, exclude_if=_ignore_none)
    text_after: str | None = Field(default=None, exclude_if=_ignore_none)

    bounding_rects: list[ScreenRect] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)


class CursorToolResult(BaseModel):
    success: bool
    mode: str
    message: str

    verified: bool | None = Field(default=None, exclude_if=_ignore_none)

    requested: dict[str, Any] = Field(default_factory=dict)

    target: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Target calculated on the client-side UIA range after applying document-boundary "
            "clamping. This does not prove that the provider applied the target."
        ),
    )

    actual: dict[str, Any] = Field(
        default_factory=dict,
        description="Real caret or selection position read back from the provider after the write.",
    )

    before: CursorSnapshot | None = None
    after: CursorSnapshot | None = None

    warnings: list[str] = Field(default_factory=list)


# endregion

# ---------------------------------------------------------------------------
# COM worker
# ---------------------------------------------------------------------------

COINIT_MULTITHREADED = 0x0
RPC_E_CHANGED_MODE = 0x80010106

_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="uia-text-cursor",
)

# Per-worker-thread COM state. The executor thread lives for the process
# lifetime, so COM is initialized once and the IUIAutomation client is cached
# and reused across calls instead of being recreated (and the apartment
# re-initialized) on every invocation.
_thread_state = threading.local()


def co_initialize_mta() -> bool:
    ole32 = ctypes.windll.ole32

    ole32.CoInitializeEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    ole32.CoInitializeEx.restype = ctypes.c_long

    hr = int(
        ole32.CoInitializeEx(
            None,
            COINIT_MULTITHREADED,
        )
    )

    unsigned_hr = hr & 0xFFFFFFFF

    if unsigned_hr == RPC_E_CHANGED_MODE:
        raise RuntimeError("The UIA worker thread has an incompatible COM apartment.")

    if unsigned_hr & 0x80000000:
        raise OSError(f"CoInitializeEx failed: 0x{unsigned_hr:08X}")

    return True


def co_uninitialize() -> None:
    ctypes.windll.ole32.CoUninitialize()


def get_thread_automation() -> tuple[UIAModule, UIAAutomationObject]:
    """Return this worker thread's cached UIA client, creating it on first use.

    COM is initialized (MTA) exactly once per thread and the IUIAutomation
    client is cached, so repeated calls reuse the same client rather than
    paying for CoInitializeEx + CreateObject on every invocation. The client
    is long-lived and never goes stale; only the focused element and its text
    ranges are re-fetched per call.
    """
    if not getattr(_thread_state, "com_initialized", False):
        co_initialize_mta()
        _thread_state.com_initialized = True

    automation = getattr(_thread_state, "automation", None)
    if automation is None:
        automation = create_automation()
        _thread_state.automation = automation

    return automation


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------


def endpoint_offset(
    caret_info: UIACaretInfo,
    endpoint: TextRangeEndpoint,
) -> Optional[int]:
    """Return the character offset from the document start to the given
    endpoint (Start or End) of the caret/selection range."""
    marker = caret_info.text_range.clone()

    if endpoint == TextRangeEndpoint.End:
        marker.collapse_range(to_end=True)
    else:
        marker.collapse_range(to_end=False)

    return range_start_offset(marker)


def make_snapshot(
    caret_info: UIACaretInfo,
    context_chars: int,
    *,
    include_context: bool = True,
    include_selected_text: bool = True,
) -> CursorSnapshot:
    start = endpoint_offset(caret_info, TextRangeEndpoint.Start)
    # A caret is a degenerate range: both endpoints resolve to the same offset,
    # and only caret_offset_units (== start) is emitted. Computing the End
    # endpoint would be a second full Move(-MAX) walk back to DocumentRange
    # start for nothing, so reuse start. Only a real selection needs End.
    # (The start-is-None short-circuit keeps the unavailable-offset error below
    # from being masked by inspecting the range first.)
    if start is not None and caret_info.exact_caret:
        end = start
    else:
        end = endpoint_offset(caret_info, TextRangeEndpoint.End)

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
            selected_text = caret_info.text_range.get_text(MAX_SELECTED_TEXT_CHARS + 1)
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
            before = caret_info.text_range.text_before(context_chars)
        except COMError:
            warnings.append("The provider did not allow reading text_before. The field was omitted.")

        try:
            after = caret_info.text_range.text_after(context_chars)
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

    return CursorSnapshot(
        provider=caret_info.source,
        element_name=(caret_info.element.get_name() or None),
        type="caret" if caret_info.exact_caret else "range",
        caret_offset_units=start if caret_info.exact_caret else None,  # caret only
        selection_start_units=(start if not caret_info.exact_caret else None),  # range only
        selection_end_units=end if not caret_info.exact_caret else None,  # range only
        selected_text=selected_text,
        text_before=before,
        text_after=after,
        bounding_rects=caret_info.text_range.bounding_rectangles(),
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


# ---------------------------------------------------------------------------
# Range construction
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Applying and verifying write operations
# ---------------------------------------------------------------------------


def apply_change(caret_info: UIACaretInfo, target: TextRange):
    caret_info.element.set_focus()
    # Move() only modifies the local range.
    # Select() requests the actual caret/selection change.
    target.select()


def verify(
    caret_info: UIACaretInfo,
    target: TextRange,
    need_verify: bool,
) -> Optional[bool]:
    """Verify the applied range matches `target` by reading the selection back.

    Returns None when verification was not requested.
    """
    if not need_verify:
        return None

    actual = caret_info.text_pattern.get_first_selection()
    if actual is None:
        return False

    return target == actual


# ---------------------------------------------------------------------------
# Tool modes
# ---------------------------------------------------------------------------


def run_get_info(action: GetInfoAction) -> CursorToolResult:
    caret_info = find_caret_provider()
    snapshot = make_snapshot(caret_info, action.context_chars)

    return CursorToolResult(
        success=True,
        mode=action.mode,
        message="Caret information acquired.",
        after=snapshot,
        warnings=snapshot.warnings,
    )


class WriteActionResult(NamedTuple):
    verified: bool | None  # None means there is no verification after action
    target_info: dict[str, Any]


def apply_move_relative(action: MoveRelativeAction, caret_info: UIACaretInfo) -> WriteActionResult:
    # For a selection, resolve which endpoint the move is relative to.
    target = get_origin_from_range(caret_info, action.origin)
    target_delta = int(target.move(TextUnit.Character, action.delta))
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


def run_write(action: WriteAction) -> CursorToolResult:
    caret_info = find_caret_provider()

    before = make_snapshot(caret_info, action.context_chars)
    verified, target = apply_write(action, caret_info)
    requested = action.model_dump(
        exclude={
            "delay",
            "context_chars",
            "verify",
        }
    )

    # Always reacquire the focused provider before reporting success or a
    # verification mismatch. The values returned by TextRange.Move describe
    # only the client-side target; `actual` must come from the real provider.
    refreshed = find_caret_provider()
    after = make_snapshot(refreshed, action.context_chars)
    actual = snapshot_position(after)

    if verified is False:
        raise TextCursorVerificationError(
            "The provider accepted the operation, but read-back verification "
            "showed that the real caret/selection did not match the calculated "
            f"target. Requested: {requested}; target: {target}; actual: {actual}."
        )

    warnings = list(dict.fromkeys([*before.warnings, *after.warnings]))

    return CursorToolResult(
        success=True,
        mode=action.mode,
        message="Operation applied.",
        verified=verified,
        requested=requested,
        target=target,
        actual=actual,
        before=before,
        after=after,
        warnings=warnings,
    )


def execute_sync(action: CursorAction) -> CursorToolResult:
    # COM is initialized once per worker thread and the UIA client is cached
    # on first use (see get_thread_automation); there is no longer a per-call
    # CoInitialize/CoUninitialize or CreateObject.
    if isinstance(action, GetInfoAction):
        return run_get_info(action)

    return run_write(action)


async def run_tool(action: CursorAction) -> CursorToolResult:
    """
    Inspect or manipulate the focused Windows text control through UIA.
    Modes:
    - get_info
    - move_relative
    - move_absolute
    - select_relative
    - select_absolute
    - select_all
    - collapse_selection
    Every mode accepts `delay`, expressed in seconds. The delay occurs before
    the focused UIA element is located, so the caller can focus the target
    control during that interval.
    Absolute move/select inputs and returned offsets both use provider-defined
    UIA TextUnit_Character steps from DocumentRange start. Returned offsets can
    be passed directly to absolute move/select actions.
    """
    if action.delay > 0:
        await asyncio.sleep(action.delay)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _EXECUTOR,
        execute_sync,
        action,
    )
