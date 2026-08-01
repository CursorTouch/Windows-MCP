"""IAccessible (MSAA / IA2) tree traversal for Firefox web DOM extraction.

Firefox exposes its browser chrome via UIA but its web DOM only via MSAA / IAccessible2.
The UIA traversal in :mod:`windows_mcp.tree.service` looks for an element with
``AutomationId == "RootWebArea"`` (the Chrome / Edge convention) to enter "DOM mode" —
Firefox has no such marker, so DOM extraction is skipped entirely.

This module fetches the root ``IAccessible`` from a Firefox window handle via
``AccessibleObjectFromWindow`` (oleacc.dll) and walks the resulting tree, collecting
text and interactive elements in the same shape used by the UIA path:

- ``dom_informative_nodes`` — list[TextElementNode]
- ``dom_interactive_nodes`` — list[TreeElementNode]
- ``dom_bounding_box`` — BoundingBox of the web content area
"""

from __future__ import annotations

import logging
from typing import Optional

from _ctypes import COMError

from windows_mcp.tree.views import BoundingBox, TextElementNode, TreeElementNode

logger = logging.getLogger(__name__)


_IAccessible = None
_oleacc_signatures_set = False


def _iaccessible():
    """Lazily resolve the IAccessible interface class and bind oleacc signatures.

    Deferred until first use so the module imports cleanly without a COM dispatch.
    The signature bind is idempotent but guarded so we don't pay the attribute
    writes on every traverse_window call.
    """
    global _IAccessible, _oleacc_signatures_set
    if _IAccessible is None:
        import ctypes
        from ctypes import wintypes

        import comtypes  # noqa: F401  (forces COM module init)
        from comtypes import GUID

        from windows_mcp.uia.comtypes_cache import safe_get_module

        safe_get_module("oleacc.dll", required_attr="IAccessible")
        from comtypes.gen.Accessibility import IAccessible  # type: ignore

        _IAccessible = IAccessible

        oleacc = ctypes.windll.oleacc
        oleacc.AccessibleObjectFromWindow.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.POINTER(IAccessible)),
        ]
        oleacc.AccessibleObjectFromWindow.restype = ctypes.HRESULT
        _oleacc_signatures_set = True
    return _IAccessible


# OBJID_CLIENT — request the client-area accessible object
OBJID_CLIENT = 0xFFFFFFFC

# CHILDID_SELF — refer to the element itself (vs. a simple child index)
CHILDID_SELF = 0


# ---------------------------------------------------------------------------
# MSAA role / state constants (oleacc.h)
# ---------------------------------------------------------------------------

ROLE_DOCUMENT = 0x0F
ROLE_GROUPING = 0x14
ROLE_TOOLBAR = 0x16
ROLE_TABLE = 0x18
ROLE_LINK = 0x1E
ROLE_LIST = 0x21
ROLE_LISTITEM = 0x22
ROLE_OUTLINE = 0x23
ROLE_OUTLINEITEM = 0x24
ROLE_PAGETAB = 0x25
ROLE_GRAPHIC = 0x28
ROLE_STATICTEXT = 0x29
ROLE_TEXT = 0x2A
ROLE_PUSHBUTTON = 0x2B
ROLE_CHECKBUTTON = 0x2C
ROLE_RADIOBUTTON = 0x2D
ROLE_COMBOBOX = 0x2E
ROLE_DROPLIST = 0x2F
ROLE_SLIDER = 0x33
ROLE_BUTTONDROPDOWN = 0x38
ROLE_PAGETABLIST = 0x3C
ROLE_SPLITBUTTON = 0x3E

STATE_SYSTEM_FOCUSED = 0x00000004
STATE_SYSTEM_INVISIBLE = 0x00008000
STATE_SYSTEM_OFFSCREEN = 0x00010000
STATE_SYSTEM_FOCUSABLE = 0x00100000
STATE_SYSTEM_LINKED = 0x00400000


ROLE_NAMES: dict[int, str] = {
    ROLE_DOCUMENT: "document",
    ROLE_GROUPING: "grouping",
    ROLE_TOOLBAR: "toolbar",
    ROLE_TABLE: "table",
    ROLE_LINK: "link",
    ROLE_LIST: "list",
    ROLE_LISTITEM: "list item",
    ROLE_OUTLINE: "outline",
    ROLE_OUTLINEITEM: "outline item",
    ROLE_PAGETAB: "tab",
    ROLE_GRAPHIC: "graphic",
    ROLE_STATICTEXT: "text",
    ROLE_TEXT: "text",
    ROLE_PUSHBUTTON: "button",
    ROLE_CHECKBUTTON: "check box",
    ROLE_RADIOBUTTON: "radio button",
    ROLE_COMBOBOX: "combo box",
    ROLE_DROPLIST: "drop list",
    ROLE_SLIDER: "slider",
    ROLE_BUTTONDROPDOWN: "button",
    ROLE_PAGETABLIST: "tab list",
    ROLE_SPLITBUTTON: "button",
}

# Disambiguated from windows_mcp.tree.config.INTERACTIVE_ROLES which holds UIA
# (string) roles. These are MSAA integer roles for the IA2 fallback path.
INTERACTIVE_MSAA_ROLES: set[int] = {
    ROLE_LINK,
    ROLE_PUSHBUTTON,
    ROLE_CHECKBUTTON,
    ROLE_RADIOBUTTON,
    ROLE_COMBOBOX,
    ROLE_DROPLIST,
    ROLE_SLIDER,
    ROLE_LISTITEM,
    ROLE_OUTLINEITEM,
    ROLE_PAGETAB,
    ROLE_BUTTONDROPDOWN,
    ROLE_SPLITBUTTON,
}

INFORMATIVE_MSAA_ROLES: set[int] = {
    ROLE_STATICTEXT,
    ROLE_TEXT,
}

# Walk caps — Firefox's a11y tree can be huge on heavy pages.
# Caps apply to the active document subtree; chrome and inactive tabs are pruned first.
MAX_DEPTH = 500
MAX_NODES = 30000


def role_name(role: object) -> str:
    """Map an ``accRole`` result to a friendly control-type name.

    Args:
        role: Either an ``int`` role constant (the usual MSAA case, e.g.
            ``ROLE_LINK``) or a ``BSTR`` string (Firefox returns these for IA2
            role extensions like ``"heading"`` or ``"article"``).

    Returns:
        Lower-cased role name (e.g. ``"link"``, ``"button"``, ``"heading"``),
        or ``"unknown"`` if the role can't be mapped or is empty.
    """
    if isinstance(role, str):
        return role.strip().lower() or "unknown"
    if isinstance(role, int):
        return ROLE_NAMES.get(role, "unknown")
    return "unknown"


# ---------------------------------------------------------------------------
# IAccessible acquisition
# ---------------------------------------------------------------------------


def _accessible_object_from_window(hwnd: int):
    """Wrap oleacc!AccessibleObjectFromWindow and return a comtypes IAccessible pointer."""
    import ctypes

    iface = _iaccessible()  # binds argtypes/restype on first call
    oleacc = ctypes.windll.oleacc

    pacc = ctypes.POINTER(iface)()
    hr = oleacc.AccessibleObjectFromWindow(
        hwnd,
        OBJID_CLIENT,
        ctypes.byref(iface._iid_),
        ctypes.byref(pacc),
    )
    if hr != 0:
        raise OSError(f"AccessibleObjectFromWindow failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")
    if not pacc:
        raise RuntimeError("AccessibleObjectFromWindow returned a null pointer")
    return pacc


# ---------------------------------------------------------------------------
# Property accessors — every IAccessible call can throw COMError; isolate them.
# ---------------------------------------------------------------------------


def _acc_role(iacc) -> object:
    try:
        return iacc.accRole(CHILDID_SELF)
    except COMError:
        return -1


def _acc_state(iacc) -> int:
    try:
        state = iacc.accState(CHILDID_SELF)
        return state if isinstance(state, int) else 0
    except (COMError, TypeError, ValueError):
        return 0


def _acc_name(iacc) -> str:
    try:
        name = iacc.accName(CHILDID_SELF)
        return (name or "").strip()
    except COMError:
        return ""


def _acc_value(iacc) -> str:
    try:
        value = iacc.accValue(CHILDID_SELF)
        return (value or "").strip()
    except COMError:
        return ""


def _acc_location(iacc) -> Optional[tuple[int, int, int, int]]:
    """Return (left, top, width, height) for the element, or None on failure."""
    try:
        # accLocation has out-params; comtypes returns a tuple.
        left, top, width, height = iacc.accLocation(CHILDID_SELF)
        return int(left), int(top), int(width), int(height)
    except (COMError, TypeError, ValueError):
        return None


def _acc_child_count(iacc) -> int:
    try:
        return int(iacc.accChildCount)
    except (COMError, TypeError, ValueError):
        return 0


def _iter_children(iacc, iface):
    """Yield IAccessible children. Skips simple-child entries (no IDispatch)."""
    count = _acc_child_count(iacc)
    if count <= 0:
        return
    for index in range(1, count + 1):
        try:
            disp = iacc.accChild(index)
        except COMError:
            continue
        if disp is None:
            continue
        try:
            child = disp.QueryInterface(iface)
        except (COMError, AttributeError):
            continue
        yield child


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


def _bounding_box_from_location(
    location: tuple[int, int, int, int], clip: Optional[BoundingBox]
) -> BoundingBox:
    left, top, width, height = location
    right, bottom = left + width, top + height
    if clip is not None:
        left = max(left, clip.left)
        top = max(top, clip.top)
        right = min(right, clip.right)
        bottom = min(bottom, clip.bottom)
    width = max(0, right - left)
    height = max(0, bottom - top)
    return BoundingBox(left=left, top=top, right=right, bottom=bottom, width=width, height=height)


def _is_visible(state: int, location: Optional[tuple[int, int, int, int]]) -> bool:
    if state & (STATE_SYSTEM_INVISIBLE | STATE_SYSTEM_OFFSCREEN):
        return False
    if location is None:
        return False
    _, _, width, height = location
    return width > 0 and height > 0


class _Walker:
    """Encapsulates traversal state (caps, counts, output buffers).

    Scoping rules:
    1. Invisible / offscreen subtrees are pruned entirely (don't descend). This is
       what filters out Firefox's inactive tabs — their document subtrees are marked
       invisible.
    2. Content is only recorded while inside a ROLE_DOCUMENT subtree. This filters
       out Firefox chrome (toolbar, tab strip, URL bar, bookmarks) which would
       otherwise dominate the output.
    3. The first visible document we enter sets ``active_document_box`` — used by
       ``traverse_window`` as the DOM bounding box.
    """

    def __init__(self, window_name: str, dom_clip: Optional[BoundingBox]):
        self.window_name = window_name
        self.dom_clip = dom_clip
        self.informative: list[TextElementNode] = []
        self.interactive: list[TreeElementNode] = []
        self.seen = 0
        self.in_document = 0  # depth of nested documents (iframes etc.)
        self.active_document_box: Optional[BoundingBox] = None

    def walk(self, iacc, iface, depth: int = 0) -> None:
        if depth > MAX_DEPTH or self.seen >= MAX_NODES:
            return
        self.seen += 1

        role = _acc_role(iacc)
        state = _acc_state(iacc)
        location = _acc_location(iacc)

        # Prune entire invisible subtrees. Always descend from the root (depth==0)
        # because the window root itself may report an unusual bbox.
        if depth > 0 and not _is_visible(state, location):
            return

        role_int = role if isinstance(role, int) else -1
        is_document = role_int == ROLE_DOCUMENT
        if is_document:
            self.in_document += 1
            if self.active_document_box is None and location is not None:
                self.active_document_box = _bounding_box_from_location(location, self.dom_clip)

        # Only record content while we're inside (or at the root of) a document subtree.
        if self.in_document > 0 and _is_visible(state, location):
            self._record(iacc, role, state, location)

        for child in _iter_children(iacc, iface):
            try:
                self.walk(child, iface, depth + 1)
            except COMError as e:
                logger.debug("Skipping IA2 subtree due to COMError: %s", e)
                continue

        if is_document:
            self.in_document -= 1

    def _record(
        self,
        iacc,
        role: object,
        state: int,
        location: Optional[tuple[int, int, int, int]],
    ) -> None:
        if location is None:
            return

        role_int = role if isinstance(role, int) else -1
        role_label = role_name(role)
        name = _acc_name(iacc)

        if role_int in INFORMATIVE_MSAA_ROLES:
            if name:
                self.informative.append(TextElementNode(text=name))
            return

        if role_int in INTERACTIVE_MSAA_ROLES or (state & STATE_SYSTEM_LINKED):
            bbox = _bounding_box_from_location(location, self.dom_clip)
            if bbox.width <= 0 or bbox.height <= 0:
                return
            metadata: dict[str, object] = {
                "has_focused": bool(state & STATE_SYSTEM_FOCUSED),
            }
            value = _acc_value(iacc)
            if value and role_int == ROLE_LINK:
                metadata["url"] = value
            elif value and role_int in {ROLE_COMBOBOX, ROLE_DROPLIST, ROLE_SLIDER}:
                metadata["value"] = value
            self.interactive.append(
                TreeElementNode(
                    name=name or value or role_label,
                    control_type=role_label.title(),
                    bounding_box=bbox,
                    center=bbox.get_center(),
                    window_name=self.window_name,
                    metadata=metadata,
                )
            )
            return

        # ROLE_GRAPHIC with a non-empty alt text — surface as informative text.
        if role_int == ROLE_GRAPHIC and name:
            self.informative.append(TextElementNode(text=name))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


class IA2TraversalResult:
    """Result of a single :func:`traverse_window` call.

    Attributes:
        dom_bounding_box: Bounding box of the active document element, or
            the fallback window box if no document was found. ``None`` only
            when both inputs were ``None``.
        informative_nodes: ``TextElementNode`` list for visible non-interactive
            content (headings, paragraphs, alt text, etc.).
        interactive_nodes: ``TreeElementNode`` list for clickable / focusable
            elements (links, buttons, form fields).

    The instance is falsy when both node lists are empty — call sites use
    ``if result:`` to skip the no-DOM case.
    """

    __slots__ = ("dom_bounding_box", "informative_nodes", "interactive_nodes")

    def __init__(
        self,
        dom_bounding_box: Optional[BoundingBox],
        informative_nodes: list[TextElementNode],
        interactive_nodes: list[TreeElementNode],
    ):
        self.dom_bounding_box = dom_bounding_box
        self.informative_nodes = informative_nodes
        self.interactive_nodes = interactive_nodes

    def __bool__(self) -> bool:
        return bool(self.informative_nodes or self.interactive_nodes)


def traverse_window(
    hwnd: int,
    window_name: str,
    window_bounding_box: Optional[BoundingBox] = None,
) -> IA2TraversalResult:
    """Walk the IAccessible tree of ``hwnd`` and return DOM-shaped node lists.

    Used as a fallback for Firefox, whose web DOM is only reachable via
    MSAA / IAccessible2 (it does not expose ``RootWebArea`` through UIA).

    Args:
        hwnd: Win32 window handle of the browser window to walk.
        window_name: Title of the window, attached to each emitted node for
            downstream display and matching against active-window state.
        window_bounding_box: Outer window rectangle. Used to clip element
            rectangles to the visible area, matching what the UIA path does
            via :py:meth:`Tree.iou_bounding_box`. ``None`` disables clipping.

    Returns:
        :class:`IA2TraversalResult` containing the (possibly empty)
        informative and interactive node lists plus the bounding box of the
        active document (or ``window_bounding_box`` if no document was found).

    The function never raises: COM acquisition or walk failures are logged at
    ``WARNING`` and an empty :class:`IA2TraversalResult` is returned so callers
    can fall back to the no-DOM error path.
    """
    iface = _iaccessible()
    try:
        root = _accessible_object_from_window(hwnd)
    except (OSError, RuntimeError) as e:
        logger.warning("IA2 acquisition failed for hwnd %#x: %s", hwnd, e)
        return IA2TraversalResult(window_bounding_box, [], [])

    walker = _Walker(window_name=window_name, dom_clip=window_bounding_box)
    try:
        walker.walk(root, iface)
    except COMError as e:
        logger.warning("IA2 walk for hwnd %#x aborted with COMError: %s", hwnd, e)

    if walker.seen >= MAX_NODES:
        logger.warning(
            "IA2 walk for '%s' hit MAX_NODES=%d cap; output is truncated",
            window_name,
            MAX_NODES,
        )

    # Prefer the active document's bbox (the web content area) over the window bbox.
    # Falls back to the window bbox if no document was found in the tree.
    dom_bbox = walker.active_document_box or window_bounding_box

    return IA2TraversalResult(
        dom_bounding_box=dom_bbox,
        informative_nodes=walker.informative,
        interactive_nodes=walker.interactive,
    )
