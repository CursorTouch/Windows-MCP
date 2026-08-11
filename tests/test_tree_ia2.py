"""Tests for the IAccessible2 / MSAA traversal helpers.

Only the pure-Python helpers are exercised here — the actual COM traversal is
Windows-only and requires a live Firefox window, so it's left for manual /
integration testing.
"""

from unittest.mock import MagicMock

from windows_mcp.tree import ia2
from windows_mcp.tree.views import BoundingBox, TextElementNode, TreeElementNode


class TestRoleName:
    def test_known_int_role(self):
        assert ia2.role_name(ia2.ROLE_LINK) == "link"
        assert ia2.role_name(ia2.ROLE_PUSHBUTTON) == "button"
        assert ia2.role_name(ia2.ROLE_STATICTEXT) == "text"
        assert ia2.role_name(ia2.ROLE_LISTITEM) == "list item"

    def test_unknown_int_role(self):
        assert ia2.role_name(0xDEADBEEF) == "unknown"

    def test_bstr_role_passes_through(self):
        # Firefox can return BSTR roles like "heading", "article" for IA2 extensions.
        assert ia2.role_name("heading") == "heading"
        assert ia2.role_name("ARTICLE") == "article"
        assert ia2.role_name("  paragraph  ") == "paragraph"

    def test_empty_bstr_role(self):
        assert ia2.role_name("") == "unknown"
        assert ia2.role_name("   ") == "unknown"

    def test_none_or_other_returns_unknown(self):
        assert ia2.role_name(None) == "unknown"
        assert ia2.role_name(3.14) == "unknown"


class TestIsVisible:
    def test_visible_element(self):
        assert ia2._is_visible(state=0, location=(0, 0, 100, 50)) is True

    def test_invisible_state(self):
        assert ia2._is_visible(ia2.STATE_SYSTEM_INVISIBLE, (0, 0, 100, 50)) is False

    def test_offscreen_state(self):
        assert ia2._is_visible(ia2.STATE_SYSTEM_OFFSCREEN, (0, 0, 100, 50)) is False

    def test_invisible_or_offscreen_combined(self):
        combined = ia2.STATE_SYSTEM_INVISIBLE | ia2.STATE_SYSTEM_OFFSCREEN
        assert ia2._is_visible(combined, (0, 0, 100, 50)) is False

    def test_zero_size(self):
        assert ia2._is_visible(0, (0, 0, 0, 50)) is False
        assert ia2._is_visible(0, (0, 0, 100, 0)) is False

    def test_no_location(self):
        assert ia2._is_visible(0, None) is False

    def test_focused_visible_element(self):
        focused = ia2.STATE_SYSTEM_FOCUSED | ia2.STATE_SYSTEM_FOCUSABLE
        assert ia2._is_visible(focused, (10, 10, 50, 50)) is True


class TestBoundingBoxFromLocation:
    def test_fully_inside_clip(self):
        clip = BoundingBox(left=0, top=0, right=200, bottom=200, width=200, height=200)
        bb = ia2._bounding_box_from_location((50, 50, 100, 100), clip)
        assert (bb.left, bb.top, bb.right, bb.bottom) == (50, 50, 150, 150)
        assert (bb.width, bb.height) == (100, 100)

    def test_clipped_to_window(self):
        clip = BoundingBox(left=10, top=10, right=100, bottom=100, width=90, height=90)
        bb = ia2._bounding_box_from_location((-50, -50, 200, 200), clip)
        assert (bb.left, bb.top, bb.right, bb.bottom) == (10, 10, 100, 100)
        assert (bb.width, bb.height) == (90, 90)

    def test_no_clip(self):
        bb = ia2._bounding_box_from_location((10, 20, 30, 40), None)
        assert (bb.left, bb.top, bb.right, bb.bottom) == (10, 20, 40, 60)
        assert (bb.width, bb.height) == (30, 40)

    def test_non_overlapping_clip(self):
        clip = BoundingBox(left=0, top=0, right=100, bottom=100, width=100, height=100)
        bb = ia2._bounding_box_from_location((500, 500, 50, 50), clip)
        assert bb.width == 0 and bb.height == 0


class TestRoleClassification:
    def test_interactive_roles_contains_expected(self):
        for role in (
            ia2.ROLE_LINK,
            ia2.ROLE_PUSHBUTTON,
            ia2.ROLE_CHECKBUTTON,
            ia2.ROLE_RADIOBUTTON,
        ):
            assert role in ia2.INTERACTIVE_MSAA_ROLES

    def test_informative_roles_contains_text(self):
        assert ia2.ROLE_STATICTEXT in ia2.INFORMATIVE_MSAA_ROLES
        assert ia2.ROLE_TEXT in ia2.INFORMATIVE_MSAA_ROLES

    def test_disjoint_role_sets(self):
        assert not (ia2.INTERACTIVE_MSAA_ROLES & ia2.INFORMATIVE_MSAA_ROLES)


class TestWalkerRecord:
    def _build_walker(self):
        clip = BoundingBox(left=0, top=0, right=1000, bottom=1000, width=1000, height=1000)
        return ia2._Walker(window_name="Firefox", dom_clip=clip)

    def test_static_text_becomes_informative(self, monkeypatch):
        walker = self._build_walker()
        iacc = MagicMock()

        monkeypatch.setattr(ia2, "_acc_name", lambda _: "hello world")
        walker._record(iacc, role=ia2.ROLE_STATICTEXT, state=0, location=(0, 0, 100, 20))

        assert len(walker.informative) == 1
        assert isinstance(walker.informative[0], TextElementNode)
        assert walker.informative[0].text == "hello world"
        assert walker.interactive == []

    def test_link_becomes_interactive(self, monkeypatch):
        walker = self._build_walker()
        iacc = MagicMock()

        monkeypatch.setattr(ia2, "_acc_name", lambda _: "Click me")
        monkeypatch.setattr(ia2, "_acc_value", lambda _: "https://example.com")
        walker._record(iacc, role=ia2.ROLE_LINK, state=0, location=(10, 20, 80, 16))

        assert len(walker.interactive) == 1
        node = walker.interactive[0]
        assert isinstance(node, TreeElementNode)
        assert node.name == "Click me"
        assert node.control_type.lower() == "link"
        assert node.metadata["url"] == "https://example.com"
        assert node.metadata["has_focused"] is False
        assert walker.informative == []

    def test_button_with_focus(self, monkeypatch):
        walker = self._build_walker()
        iacc = MagicMock()

        monkeypatch.setattr(ia2, "_acc_name", lambda _: "Submit")
        monkeypatch.setattr(ia2, "_acc_value", lambda _: "")
        state = ia2.STATE_SYSTEM_FOCUSED | ia2.STATE_SYSTEM_FOCUSABLE
        walker._record(iacc, role=ia2.ROLE_PUSHBUTTON, state=state, location=(0, 0, 80, 30))

        assert len(walker.interactive) == 1
        assert walker.interactive[0].metadata["has_focused"] is True

    def test_graphic_with_alt_text_becomes_informative(self, monkeypatch):
        walker = self._build_walker()
        iacc = MagicMock()

        monkeypatch.setattr(ia2, "_acc_name", lambda _: "Company logo")
        walker._record(iacc, role=ia2.ROLE_GRAPHIC, state=0, location=(0, 0, 100, 100))

        assert walker.interactive == []
        assert len(walker.informative) == 1
        assert walker.informative[0].text == "Company logo"

    def test_graphic_without_alt_is_skipped(self, monkeypatch):
        walker = self._build_walker()
        iacc = MagicMock()

        monkeypatch.setattr(ia2, "_acc_name", lambda _: "")
        walker._record(iacc, role=ia2.ROLE_GRAPHIC, state=0, location=(0, 0, 100, 100))

        assert walker.informative == []
        assert walker.interactive == []

    def test_unknown_role_is_skipped(self, monkeypatch):
        walker = self._build_walker()
        iacc = MagicMock()

        monkeypatch.setattr(ia2, "_acc_name", lambda _: "irrelevant")
        monkeypatch.setattr(ia2, "_acc_value", lambda _: "")
        walker._record(iacc, role=0xDEADBEEF, state=0, location=(0, 0, 100, 100))

        assert walker.informative == []
        assert walker.interactive == []

    def test_link_with_no_visible_bbox_skipped(self, monkeypatch):
        # Element location is outside the clip — clipped width/height go to zero.
        clip = BoundingBox(left=0, top=0, right=100, bottom=100, width=100, height=100)
        walker = ia2._Walker(window_name="Firefox", dom_clip=clip)
        iacc = MagicMock()

        monkeypatch.setattr(ia2, "_acc_name", lambda _: "Offscreen link")
        monkeypatch.setattr(ia2, "_acc_value", lambda _: "https://x")
        walker._record(iacc, role=ia2.ROLE_LINK, state=0, location=(500, 500, 80, 16))

        assert walker.interactive == []


class TestWalkerScoping:
    """The walker must (1) prune invisible subtrees and (2) only record inside documents."""

    def _make_iacc(self, role, state, location, name="", value="", children=()):
        """Build a fake IAccessible node for walker traversal tests."""
        node = MagicMock()
        node._fake = {
            "role": role,
            "state": state,
            "location": location,
            "name": name,
            "value": value,
            "children": children,
        }
        return node

    def _walk_with_fake_tree(self, root, walker, monkeypatch):
        """Drive the walker against a tree of fake nodes (no COM)."""
        monkeypatch.setattr(ia2, "_acc_role", lambda n: n._fake["role"])
        monkeypatch.setattr(ia2, "_acc_state", lambda n: n._fake["state"])
        monkeypatch.setattr(ia2, "_acc_location", lambda n: n._fake["location"])
        monkeypatch.setattr(ia2, "_acc_name", lambda n: n._fake["name"])
        monkeypatch.setattr(ia2, "_acc_value", lambda n: n._fake["value"])
        monkeypatch.setattr(ia2, "_iter_children", lambda n, _iface: iter(n._fake["children"]))
        walker.walk(root, iface=object(), depth=0)

    def test_chrome_outside_document_not_recorded(self, monkeypatch):
        """Toolbar button (outside any document) must be excluded."""
        # window -> toolbar button (no document)
        button = self._make_iacc(ia2.ROLE_PUSHBUTTON, 0, (0, 0, 50, 30), name="Reload")
        root = self._make_iacc(0x10, 0, (0, 0, 1000, 800), children=[button])  # ROLE_PANE

        walker = ia2._Walker(window_name="Firefox", dom_clip=None)
        self._walk_with_fake_tree(root, walker, monkeypatch)

        assert walker.interactive == []
        assert walker.informative == []

    def test_link_inside_document_recorded(self, monkeypatch):
        """A link inside a ROLE_DOCUMENT subtree should be captured."""
        link = self._make_iacc(
            ia2.ROLE_LINK,
            0,
            (10, 100, 200, 20),
            name="YouTube video title",
            value="https://youtu.be/abc",
        )
        document = self._make_iacc(ia2.ROLE_DOCUMENT, 0, (0, 50, 1000, 700), children=[link])
        root = self._make_iacc(0x10, 0, (0, 0, 1000, 800), children=[document])

        walker = ia2._Walker(window_name="Firefox", dom_clip=None)
        self._walk_with_fake_tree(root, walker, monkeypatch)

        assert len(walker.interactive) == 1
        assert walker.interactive[0].name == "YouTube video title"
        assert walker.interactive[0].metadata["url"] == "https://youtu.be/abc"

    def test_invisible_document_subtree_pruned(self, monkeypatch):
        """An invisible document (inactive Firefox tab) and its children must be skipped entirely."""
        # Gmail-tab content nested inside an INVISIBLE document — simulates an inactive tab.
        inactive_link = self._make_iacc(
            ia2.ROLE_LINK,
            0,
            (0, 0, 100, 20),
            name="Inbox",
            value="https://gmail",
        )
        inactive_doc = self._make_iacc(
            ia2.ROLE_DOCUMENT,
            ia2.STATE_SYSTEM_INVISIBLE,
            (0, 50, 1000, 700),
            children=[inactive_link],
        )
        # Active tab — visible document with its own link.
        active_link = self._make_iacc(
            ia2.ROLE_LINK,
            0,
            (0, 0, 100, 20),
            name="Search result",
            value="https://yt",
        )
        active_doc = self._make_iacc(
            ia2.ROLE_DOCUMENT,
            0,
            (0, 50, 1000, 700),
            children=[active_link],
        )
        root = self._make_iacc(
            0x10,
            0,
            (0, 0, 1000, 800),
            children=[inactive_doc, active_doc],
        )

        walker = ia2._Walker(window_name="Firefox", dom_clip=None)
        self._walk_with_fake_tree(root, walker, monkeypatch)

        # Inactive tab's link must not be captured; only the active tab's link.
        names = [n.name for n in walker.interactive]
        assert "Inbox" not in names
        assert names == ["Search result"]

    def test_offscreen_subtree_pruned(self, monkeypatch):
        """An offscreen document subtree is treated the same as invisible."""
        offscreen_link = self._make_iacc(
            ia2.ROLE_LINK,
            0,
            (0, 0, 100, 20),
            name="Hidden",
            value="x",
        )
        offscreen_doc = self._make_iacc(
            ia2.ROLE_DOCUMENT,
            ia2.STATE_SYSTEM_OFFSCREEN,
            (0, 50, 1000, 700),
            children=[offscreen_link],
        )
        root = self._make_iacc(0x10, 0, (0, 0, 1000, 800), children=[offscreen_doc])

        walker = ia2._Walker(window_name="Firefox", dom_clip=None)
        self._walk_with_fake_tree(root, walker, monkeypatch)

        assert walker.interactive == []

    def test_active_document_box_captured(self, monkeypatch):
        """The first visible document's bbox is stored as the DOM bounding box."""
        text = self._make_iacc(ia2.ROLE_STATICTEXT, 0, (10, 60, 100, 20), name="hello")
        document = self._make_iacc(ia2.ROLE_DOCUMENT, 0, (5, 55, 990, 700), children=[text])
        root = self._make_iacc(0x10, 0, (0, 0, 1000, 800), children=[document])

        walker = ia2._Walker(window_name="Firefox", dom_clip=None)
        self._walk_with_fake_tree(root, walker, monkeypatch)

        assert walker.active_document_box is not None
        assert walker.active_document_box.left == 5
        assert walker.active_document_box.top == 55
        assert walker.active_document_box.width == 990
        assert walker.active_document_box.height == 700

    def test_deeply_nested_content_captured(self, monkeypatch):
        """Content several levels deep inside a document still surfaces."""
        text = self._make_iacc(ia2.ROLE_STATICTEXT, 0, (0, 0, 100, 20), name="leaf")
        wrap3 = self._make_iacc(0x14, 0, (0, 0, 100, 20), children=[text])
        wrap2 = self._make_iacc(0x14, 0, (0, 0, 100, 20), children=[wrap3])
        wrap1 = self._make_iacc(0x14, 0, (0, 0, 100, 20), children=[wrap2])
        document = self._make_iacc(ia2.ROLE_DOCUMENT, 0, (0, 50, 1000, 700), children=[wrap1])
        root = self._make_iacc(0x10, 0, (0, 0, 1000, 800), children=[document])

        walker = ia2._Walker(window_name="Firefox", dom_clip=None)
        self._walk_with_fake_tree(root, walker, monkeypatch)

        assert [n.text for n in walker.informative] == ["leaf"]


class TestTraversalResult:
    def test_truthiness_empty(self):
        result = ia2.IA2TraversalResult(
            dom_bounding_box=None, informative_nodes=[], interactive_nodes=[]
        )
        assert bool(result) is False

    def test_truthiness_with_informative(self):
        result = ia2.IA2TraversalResult(
            dom_bounding_box=None,
            informative_nodes=[TextElementNode(text="hi")],
            interactive_nodes=[],
        )
        assert bool(result) is True

    def test_truthiness_with_interactive(self):
        bb = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
        result = ia2.IA2TraversalResult(
            dom_bounding_box=bb,
            informative_nodes=[],
            interactive_nodes=[
                TreeElementNode(
                    bounding_box=bb,
                    center=bb.get_center(),
                    name="X",
                    control_type="link",
                    window_name="Firefox",
                )
            ],
        )
        assert bool(result) is True


def test_traverse_window_returns_empty_when_com_unavailable(monkeypatch):
    """If AccessibleObjectFromWindow fails (e.g. no oleacc.dll), return an empty result."""

    def _raise_os_error(_hwnd: int):
        raise OSError("AccessibleObjectFromWindow failed: HRESULT=0x80004005")

    monkeypatch.setattr(ia2, "_accessible_object_from_window", _raise_os_error)
    monkeypatch.setattr(ia2, "_iaccessible", lambda: object)

    bb = BoundingBox(left=0, top=0, right=10, bottom=10, width=10, height=10)
    result = ia2.traverse_window(hwnd=0xABCD, window_name="Firefox", window_bounding_box=bb)
    assert result.informative_nodes == []
    assert result.interactive_nodes == []
    assert result.dom_bounding_box == bb
    assert bool(result) is False


def test_traverse_window_returns_empty_when_runtime_error(monkeypatch):
    """If AccessibleObjectFromWindow returns null, return an empty result."""

    def _raise_runtime(_hwnd: int):
        raise RuntimeError("AccessibleObjectFromWindow returned a null pointer")

    monkeypatch.setattr(ia2, "_accessible_object_from_window", _raise_runtime)
    monkeypatch.setattr(ia2, "_iaccessible", lambda: object)

    result = ia2.traverse_window(hwnd=0, window_name="Firefox")
    assert result.dom_bounding_box is None
    assert result.informative_nodes == []
    assert result.interactive_nodes == []
