"""Snapshot's UI tree shows a browser window's own controls, not only its page.

Snapshot answers with the UI tree, so an element left out of the tree is left out of the answer.
The walk captures a browser's own controls (tabs, address bar, toolbar buttons, its dialogs) as it
does any app's: they get labels and boxes on the annotated screenshot. Without use_dom the tree
lists them; use_dom=True gives "web page elements instead of browser UI", as the tool says.

Each window below goes through the real Tree.get_state() and tree_traversal(). The UI Automation
elements are mocks that hold the cached properties the walk reads.
"""

from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

import win32gui

from windows_mcp.tree import service as tree_service
from windows_mcp.tree.budget import TreeElementBudget
from windows_mcp.tree.views import BoundingBox
from windows_mcp.uia import PropertyId, Rect

BROWSER = 0x3011E
SCREEN = BoundingBox(left=0, top=0, right=1920, bottom=1080, width=1920, height=1080)
# LegacyIAccessible roles of the elements below.
ROLES = {43: "PushButton", 42: "Text", 30: "Link", 15: "Document", 22: "ToolBar"}


def _element(kind, localized_type, name, rect, role=None, children=(), automation_id=""):
    element = MagicMock()
    element.CachedIsOffscreen = False
    element.CachedIsControlElement = True
    element.CachedIsEnabled = True
    element.CachedIsKeyboardFocusable = True
    element.CachedHasKeyboardFocus = False
    element.CachedControlTypeName = kind
    element.CachedLocalizedControlType = localized_type
    element.CachedName = element.Name = name
    element.CachedAutomationId = automation_id
    element.CachedBoundingRectangle = rect
    element.CachedAcceleratorKey = ""
    element.CachedHelpText = ""
    element.GetCachedPattern.return_value = None  # nothing here scrolls
    element.GetFirstChildControl.return_value = None
    cached = {PropertyId.LegacyIAccessibleRoleProperty: role}
    element.GetCachedPropertyValue.side_effect = cached.get
    element.children = list(children)
    return element


def _browser_window(title, page_automation_id):
    """A browser window: a toolbar with Back and the address bar, above a page with a link."""
    back = _element("ButtonControl", "button", "Back", Rect(10, 45, 40, 75), role=43)
    address = _element(
        "EditControl", "edit", "Address and search bar", Rect(120, 45, 900, 75), role=42
    )
    toolbar = _element(
        "ToolBarControl", "tool bar", "App bar", Rect(0, 40, 1280, 80), 22, [back, address]
    )
    text = _element("TextControl", "text", "Example text.", Rect(100, 250, 700, 270))
    link = _element("HyperlinkControl", "link", "More info", Rect(100, 300, 300, 320), role=30)
    page = _element(
        "DocumentControl", "document", "Example", Rect(0, 90, 1280, 800), 15, [text, link]
    )
    page.CachedAutomationId = page_automation_id
    window = _element("WindowControl", "window", title, Rect(0, 0, 1280, 800))
    window.children = [toolbar, page]
    window.BoundingRectangle = Rect(0, 0, 1280, 800)
    return window


def _capture(window, use_dom, monkeypatch):
    monkeypatch.delenv("WINDOWS_MCP_EXCLUDE_PROCESSES", raising=False)
    monkeypatch.delenv("WINDOWS_MCP_MAX_TREE_ELEMENTS", raising=False)
    tree = object.__new__(tree_service.Tree)
    tree.desktop = SimpleNamespace(
        get_screen_box=lambda: SCREEN,
        is_window_browser=lambda node: True,
    )
    tree.element_budget = TreeElementBudget(500)
    helper = tree_service.CachedControlHelper
    cache_requests = SimpleNamespace(create_tree_traversal_cache=SimpleNamespace)
    with (
        mock.patch.object(tree_service, "ControlFromHandle", lambda handle: window),
        mock.patch.object(win32gui, "IsWindow", lambda handle: True),
        mock.patch.object(tree_service, "CacheRequestFactory", cache_requests),
        mock.patch.object(helper, "build_cached_control", lambda node, request: node),
        mock.patch.object(helper, "get_cached_children", lambda node, request: node.children),
        mock.patch.object(tree_service, "AccessibleRoleNames", ROLES),
    ):
        return tree.get_state(BROWSER, [], use_dom=use_dom)


TOOLBAR_LINES = [
    '        ├── tool bar "App bar"',
    '        │   ├── (25,60) button "Back"  [action: click]',
    '        │   └── (510,60) edit "Address and search bar"  [action: fill]',
]


def test_browser_controls_are_listed_above_the_page(monkeypatch):
    window = _browser_window("Example - Google Chrome", "RootWebArea")
    tree_state = _capture(window, use_dom=False, monkeypatch=monkeypatch)

    # Labels number every element the walk captured, in the walk's order.
    labelled = [node.name for node in tree_state.interactive_nodes]
    assert labelled == ["Address and search bar", "Back", "More info"]
    assert tree_state.semantic_tree_to_string().splitlines() == [
        "desktop",
        '    └── window "Example - Google Chrome"',
        *TOOLBAR_LINES,
        '        ├── (200,310) link "More info"  [action: click]',
        '        └── text "Example text."',
    ]


def test_page_without_root_web_area_is_listed(monkeypatch):
    """Firefox has no RootWebArea (see the IA2 fallback), so its page is walked as plain UIA."""
    window = _browser_window("Example - Mozilla Firefox", "")
    tree_state = _capture(window, use_dom=False, monkeypatch=monkeypatch)

    labelled = [node.name for node in tree_state.interactive_nodes]
    assert labelled == ["More info", "Address and search bar", "Back"]
    assert tree_state.semantic_tree_to_string().splitlines() == [
        "desktop",
        '    └── window "Example - Mozilla Firefox"',
        *TOOLBAR_LINES,
        '        └── (200,310) link "More info"  [action: click]',
    ]


def test_use_dom_lists_the_page_alone(monkeypatch):
    window = _browser_window("Example - Google Chrome", "RootWebArea")
    tree_state = _capture(window, use_dom=True, monkeypatch=monkeypatch)

    assert [node.name for node in tree_state.interactive_nodes] == ["More info"]
    assert tree_state.semantic_tree_to_string().splitlines() == [
        "desktop",
        '    └── window "Example - Google Chrome"',
        '        ├── (200,310) link "More info"  [action: click]',
        '        └── text "Example text."',
    ]
