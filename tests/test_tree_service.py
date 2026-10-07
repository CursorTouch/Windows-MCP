from _ctypes import COMError
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from windows_mcp.desktop.views import Size
from windows_mcp.tree.budget import TreeElementBudget
from windows_mcp.tree.config import THREAD_MAX_RETRIES
from windows_mcp.tree.service import (
    Tree,
    _excluded_process_names,
    _is_comtypes_variant_ord_typeerror,
    _window_process_name,
)
from windows_mcp.tree.views import BoundingBox, SemanticNode
from windows_mcp.uia import PropertyId, Rect


@pytest.fixture
def tree_instance():
    mock_desktop = MagicMock()
    mock_desktop.get_screen_size.return_value = Size(width=1920, height=1080)
    mock_desktop.get_screen_box.return_value = make_box(0, 0, 1920, 1080)
    return Tree(mock_desktop)


def make_box(left: int, top: int, right: int, bottom: int):
    return BoundingBox(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        width=right - left,
        height=bottom - top,
    )


class TestAppNameCorrection:
    def test_progman(self, tree_instance):
        assert tree_instance.app_name_correction("Progman") == "Desktop"

    def test_shell_traywnd(self, tree_instance):
        assert tree_instance.app_name_correction("Shell_TrayWnd") == "Taskbar"

    def test_shell_secondary_traywnd(self, tree_instance):
        assert tree_instance.app_name_correction("Shell_SecondaryTrayWnd") == "Taskbar"

    def test_popup_window_site_bridge(self, tree_instance):
        assert (
            tree_instance.app_name_correction("Microsoft.UI.Content.PopupWindowSiteBridge")
            == "Context Menu"
        )

    def test_passthrough(self, tree_instance):
        assert tree_instance.app_name_correction("Notepad") == "Notepad"
        assert tree_instance.app_name_correction("Calculator") == "Calculator"


class TestIouBoundingBox:
    def test_full_overlap(self, tree_instance):
        window = Rect(0, 0, 500, 500)
        element = Rect(100, 100, 200, 200)
        result = tree_instance.iou_bounding_box(window, element)
        assert result.left == 100
        assert result.top == 100
        assert result.right == 200
        assert result.bottom == 200
        assert result.width == 100
        assert result.height == 100

    def test_partial_overlap(self, tree_instance):
        window = Rect(0, 0, 150, 150)
        element = Rect(100, 100, 200, 200)
        result = tree_instance.iou_bounding_box(window, element)
        assert result.left == 100
        assert result.top == 100
        assert result.right == 150
        assert result.bottom == 150
        assert result.width == 50
        assert result.height == 50

    def test_no_overlap(self, tree_instance):
        window = Rect(0, 0, 50, 50)
        element = Rect(100, 100, 200, 200)
        result = tree_instance.iou_bounding_box(window, element)
        assert result.width == 0
        assert result.height == 0

    def test_screen_clamping(self, tree_instance):
        # Element extends beyond screen (1920x1080)
        window = Rect(0, 0, 2000, 2000)
        element = Rect(1900, 1060, 2000, 1200)
        result = tree_instance.iou_bounding_box(window, element)
        assert result.left == 1900
        assert result.top == 1060
        assert result.right == 1920
        assert result.bottom == 1080
        assert result.width == 20
        assert result.height == 20

    def test_screen_box_keeps_virtual_screen_origin(self):
        mock_desktop = MagicMock()
        mock_desktop.get_screen_size.return_value = Size(width=3840, height=1080)
        mock_desktop.get_screen_box.return_value = make_box(-1920, 0, 1920, 1080)

        tree = Tree(mock_desktop)
        result = tree.iou_bounding_box(
            Rect(-1920, 0, 0, 1080),
            Rect(-100, 100, 100, 200),
        )

        assert result.left == -100
        assert result.top == 100
        assert result.right == 0
        assert result.bottom == 200


def _type_error_from(filename: str) -> TypeError:
    namespace = {}
    code = compile("def trigger():\n    ord('hello')\n", filename, "exec")
    exec(code, namespace)

    with pytest.raises(TypeError) as exc_info:
        namespace["trigger"]()

    return exc_info.value


class TestComtypesVariantOrdTypeError:
    def test_matches_comtypes_automation_traceback(self):
        error = _type_error_from(
            "C:/Python313/Lib/site-packages/comtypes/automation.py",
        )

        assert _is_comtypes_variant_ord_typeerror(error) is True

    def test_rejects_same_message_from_non_comtypes_traceback(self):
        error = _type_error_from(
            "C:/QA_Automation/Windows-MCP-PR/tests/helpers/fake_source.py",
        )

        assert _is_comtypes_variant_ord_typeerror(error) is False


class TestTreeTraversal:
    def test_unnamed_interactive_control_does_not_add_semantic_child(
        self, tree_instance, monkeypatch
    ):
        child = MagicMock()
        child.CachedIsOffscreen = False
        child.CachedControlTypeName = "ButtonControl"
        child.CachedIsControlElement = True
        child.CachedBoundingRectangle = Rect(10, 10, 110, 60)
        child.CachedIsEnabled = True
        child.CachedHasKeyboardFocus = False
        child.CachedName = "   "
        child.CachedLocalizedControlType = "button"
        child.CachedAcceleratorKey = ""
        child.GetCachedPropertyValue.return_value = 43

        semantic_root = SemanticNode(
            control_type="Window",
            element_type="window",
            name="Window",
            window_name="Window",
        )

        monkeypatch.setattr(
            "windows_mcp.tree.service.CachedControlHelper.get_cached_children",
            lambda node, cache_request: [],
        )
        monkeypatch.setattr(
            "windows_mcp.tree.service.random_point_within_bounding_box",
            lambda node, scale_factor: (60, 35),
        )
        monkeypatch.setattr("windows_mcp.tree.service.AccessibleRoleNames", {43: "PushButton"})

        interactive_nodes = []
        tree_instance.tree_traversal(
            child,
            Rect(0, 0, 200, 200),
            "Window",
            False,
            interactive_nodes,
            [],
            [],
            [],
            current_semantic_node=semantic_root,
        )

        assert interactive_nodes == []
        assert semantic_root.children == []

    def test_stale_uia_subtree_is_pruned(self, tree_instance):
        class StaleNode:
            @property
            def CachedIsOffscreen(self):
                raise COMError(
                    -2147220991,
                    "An event was unable to invoke any of the subscribers",
                    (None, None, None, 0, None),
                )

        tree_instance.tree_traversal(
            StaleNode(),
            Rect(0, 0, 200, 200),
            "Window",
            False,
            [],
            [],
            [],
            [],
        )


def _make_button_child(name: str, left: int) -> MagicMock:
    child = MagicMock()
    child.CachedIsOffscreen = False
    child.CachedControlTypeName = "ButtonControl"
    child.CachedIsControlElement = True
    child.CachedBoundingRectangle = Rect(left, 10, left + 40, 60)
    child.CachedIsEnabled = True
    child.CachedHasKeyboardFocus = False
    child.CachedName = name
    child.CachedLocalizedControlType = "button"
    child.CachedAcceleratorKey = ""
    child.CachedHelpText = ""
    child.GetCachedPropertyValue.return_value = 43
    return child


def _make_text_child(text: str, left: int) -> MagicMock:
    child = MagicMock()
    child.CachedIsOffscreen = False
    child.CachedControlTypeName = "TextControl"
    child.CachedIsControlElement = True
    child.CachedBoundingRectangle = Rect(left, 10, left + 40, 60)
    child.CachedIsEnabled = True
    child.CachedIsKeyboardFocusable = False
    child.CachedName = text
    return child


def _make_pane_parent() -> MagicMock:
    parent = MagicMock()
    parent.CachedIsOffscreen = False
    parent.CachedControlTypeName = "PaneControl"
    parent.CachedIsControlElement = True
    parent.CachedBoundingRectangle = Rect(0, 0, 500, 500)
    parent.CachedIsEnabled = True
    parent.CachedIsKeyboardFocusable = False
    parent.CachedName = ""
    # Disable the scrollable-container branch — irrelevant to this test and
    # would otherwise call random_point_within_bounding_box on an unconfigured mock.
    parent.GetCachedPattern.return_value = None
    return parent


class TestElementBudgetStopsTraversal:
    """A huge flat list/grid (e.g. thousands of UIA rows) must not be walked in full —
    see budget.py. These tests exercise the wiring in tree_traversal / get_window_wise_nodes.
    """

    def test_stops_appending_and_recursing_once_budget_exhausted(
        self, tree_instance, monkeypatch
    ):
        parent = _make_pane_parent()
        children = [_make_button_child(f"btn{i}", 10 * i) for i in range(5)]

        def fake_get_children(node, cache_request):
            return children if node is parent else []

        monkeypatch.setattr(
            "windows_mcp.tree.service.CachedControlHelper.get_cached_children",
            fake_get_children,
        )
        monkeypatch.setattr("windows_mcp.tree.service.AccessibleRoleNames", {43: "PushButton"})

        tree_instance.element_budget = TreeElementBudget(limit=3)

        interactive_nodes = []
        tree_instance.tree_traversal(
            parent,
            Rect(0, 0, 500, 500),
            "Window",
            False,
            interactive_nodes,
            [],
            [],
            [],
        )

        assert len(interactive_nodes) == 3
        assert tree_instance.element_budget.truncated is True
        assert tree_instance.element_budget.count == 3

    def test_all_elements_captured_when_under_budget(self, tree_instance, monkeypatch):
        parent = _make_pane_parent()
        children = [_make_button_child(f"btn{i}", 10 * i) for i in range(5)]

        def fake_get_children(node, cache_request):
            return children if node is parent else []

        monkeypatch.setattr(
            "windows_mcp.tree.service.CachedControlHelper.get_cached_children",
            fake_get_children,
        )
        monkeypatch.setattr("windows_mcp.tree.service.AccessibleRoleNames", {43: "PushButton"})

        tree_instance.element_budget = TreeElementBudget(limit=10)

        interactive_nodes = []
        tree_instance.tree_traversal(
            parent,
            Rect(0, 0, 500, 500),
            "Window",
            False,
            interactive_nodes,
            [],
            [],
            [],
        )

        assert len(interactive_nodes) == 5
        assert tree_instance.element_budget.truncated is False

    def test_dom_informative_text_nodes_consume_budget(self, tree_instance, monkeypatch):
        # Text-heavy DOM pages must not bypass the budget just because the nodes
        # are informative rather than interactive — see PR review finding on
        # unbudgeted `dom_informative_nodes.append(TextElementNode(...))`.
        parent = _make_pane_parent()
        children = [_make_text_child(f"paragraph {i}", 10 * i) for i in range(5)]

        def fake_get_children(node, cache_request):
            return children if node is parent else []

        monkeypatch.setattr(
            "windows_mcp.tree.service.CachedControlHelper.get_cached_children",
            fake_get_children,
        )

        tree_instance.element_budget = TreeElementBudget(limit=3)

        dom_informative_nodes = []
        tree_instance.tree_traversal(
            parent,
            Rect(0, 0, 500, 500),
            "Window",
            True,
            [],
            [],
            [],
            dom_informative_nodes,
            is_dom=True,
        )

        assert len(dom_informative_nodes) == 3
        assert tree_instance.element_budget.truncated is True
        assert tree_instance.element_budget.count == 3

    def test_get_window_wise_nodes_skips_remaining_windows_once_exhausted(
        self, tree_instance, monkeypatch
    ):
        tree_instance.element_budget = TreeElementBudget(limit=1)
        tree_instance.element_budget.try_consume(1)
        assert tree_instance.element_budget.exhausted is True

        # `tree_instance.desktop` is a weakref.proxy to a mock that only lives for the
        # duration of the fixture — swap in a plain, still-alive mock before touching it.
        live_desktop = MagicMock()
        live_desktop.is_window_browser.return_value = False
        tree_instance.desktop = live_desktop

        calls = []
        monkeypatch.setattr(
            "windows_mcp.tree.service.ControlFromHandle",
            lambda handle: MagicMock(ClassName="SomeWindow", Name="Some Window"),
        )

        def fake_get_nodes(self, handle, is_browser=False, wait_time=0, use_dom=False):
            calls.append(handle)
            return ([], [], [], None)

        monkeypatch.setattr(Tree, "get_nodes", fake_get_nodes)

        tree_instance.get_window_wise_nodes(
            windows_handles=[111, 222, 333], active_window_flag=False
        )

        assert calls == []


def _with_runtime_id(control: MagicMock, runtime_id: tuple[int, ...]) -> MagicMock:
    """Report `runtime_id` from the cached RuntimeId property, keeping the role default."""

    def get_cached_property(property_id):
        if property_id == PropertyId.RuntimeIdProperty:
            return list(runtime_id)
        return 43

    control.GetCachedPropertyValue.side_effect = get_cached_property
    return control


class TestCyclicRuntimeIdGuard:
    """A provider can hand back a child whose RuntimeId equals one of its own ancestors.

    The element budget cannot bound that walk, because a cyclic pane appends no output
    nodes and so never trips the limit — see budget.py.
    """

    def test_unbounded_cycle_terminates_without_losing_its_siblings(
        self, tree_instance, monkeypatch
    ):
        """The reported shape: a pane keeps handing back a child carrying its own id.

        Unguarded this recurses until RecursionError and Snapshot never returns.
        """
        select = _with_runtime_id(_make_pane_parent(), (1, 100))
        echo = _with_runtime_id(_make_pane_parent(), (1, 100))
        option = _with_runtime_id(_make_button_child("option", 60), (1, 200))

        def fake_get_children(node, cache_request):
            if node is select:
                return [echo, option]
            if node is echo:
                return [echo]
            return []

        monkeypatch.setattr(
            "windows_mcp.tree.service.CachedControlHelper.get_cached_children",
            fake_get_children,
        )
        monkeypatch.setattr("windows_mcp.tree.service.AccessibleRoleNames", {43: "PushButton"})

        interactive_nodes = []
        tree_instance.tree_traversal(
            select,
            Rect(0, 0, 500, 500),
            "Window",
            False,
            interactive_nodes,
            [],
            [],
            [],
        )

        assert {node.name for node in interactive_nodes} == {"option"}

    def test_repeated_id_in_separate_branches_is_not_cut(self, tree_instance, monkeypatch):
        """Siblings may legitimately share an id; only an ancestor repeat is a cycle."""
        parent = _with_runtime_id(_make_pane_parent(), (1, 100))
        first = _with_runtime_id(_make_pane_parent(), (1, 200))
        second = _with_runtime_id(_make_pane_parent(), (1, 200))
        first_child = _with_runtime_id(_make_button_child("first", 10), (1, 300))
        second_child = _with_runtime_id(_make_button_child("second", 60), (1, 301))

        def fake_get_children(node, cache_request):
            if node is parent:
                return [first, second]
            if node is first:
                return [first_child]
            if node is second:
                return [second_child]
            return []

        monkeypatch.setattr(
            "windows_mcp.tree.service.CachedControlHelper.get_cached_children",
            fake_get_children,
        )
        monkeypatch.setattr("windows_mcp.tree.service.AccessibleRoleNames", {43: "PushButton"})

        interactive_nodes = []
        tree_instance.tree_traversal(
            parent,
            Rect(0, 0, 500, 500),
            "Window",
            False,
            interactive_nodes,
            [],
            [],
            [],
        )

        assert {node.name for node in interactive_nodes} == {"first", "second"}

    def test_unreadable_runtime_id_leaves_traversal_unchanged(self, tree_instance, monkeypatch):
        """Providers that expose no usable RuntimeId keep walking exactly as before."""
        parent = _make_pane_parent()
        children = [_make_button_child(f"btn{i}", 10 * i) for i in range(5)]

        def fake_get_children(node, cache_request):
            return children if node is parent else []

        monkeypatch.setattr(
            "windows_mcp.tree.service.CachedControlHelper.get_cached_children",
            fake_get_children,
        )
        monkeypatch.setattr("windows_mcp.tree.service.AccessibleRoleNames", {43: "PushButton"})

        interactive_nodes = []
        tree_instance.tree_traversal(
            parent,
            Rect(0, 0, 500, 500),
            "Window",
            False,
            interactive_nodes,
            [],
            [],
            [],
        )

        assert len(interactive_nodes) == 5


def _traversal_recorder(
    tree_instance, monkeypatch, process_names, excluded, forbidden_uia=frozenset()
):
    """Wire the stubs shared by the `WINDOWS_MCP_EXCLUDE_PROCESSES` tests.

    Args:
        tree_instance: The `Tree` fixture under test.
        monkeypatch: The pytest `monkeypatch` fixture.
        process_names: Mapping of HWND to the process name a lookup should report;
            a missing key resolves to None, mirroring an unresolvable owner.
        excluded: Value for `WINDOWS_MCP_EXCLUDE_PROCESSES`, or None to leave it unset.
        forbidden_uia: Handles whose accessibility provider must never be entered.
            Any invocation of `ControlFromHandle` for them is recorded and then
            raises.

    Returns:
        A namespace with the handles passed to `get_nodes` (`traversed`), every
        handle `ControlFromHandle` was invoked for (`uia_attempts` — the entry
        point into a window's accessibility provider), and the handles whose
        process name was resolved (`lookups`).
    """
    live_desktop = MagicMock()
    live_desktop.is_window_browser.return_value = False
    # `tree_instance.desktop` is a weakref.proxy to a mock that only lives for the
    # duration of the fixture — swap in a plain, still-alive mock before touching it.
    tree_instance.desktop = live_desktop

    if excluded is None:
        monkeypatch.delenv("WINDOWS_MCP_EXCLUDE_PROCESSES", raising=False)
    else:
        monkeypatch.setenv("WINDOWS_MCP_EXCLUDE_PROCESSES", excluded)

    lookups = []

    def fake_window_process_name(handle):
        lookups.append(handle)
        return process_names.get(handle)

    monkeypatch.setattr("windows_mcp.tree.service._window_process_name", fake_window_process_name)

    uia_attempts = []

    def fake_control_from_handle(handle):
        # Recorded *before* the forbidden check: get_window_wise_nodes wraps this
        # call in a broad `except Exception`, so a raise on its own would be
        # swallowed and a forbidden handle would leave no trace in the test.
        uia_attempts.append(handle)
        if handle in forbidden_uia:
            raise AssertionError(f"UIA touched excluded process (handle {handle})")
        return MagicMock(ClassName="SomeWindow", Name="Some Window")

    monkeypatch.setattr("windows_mcp.tree.service.ControlFromHandle", fake_control_from_handle)

    traversed = []

    def fake_get_nodes(self, handle, is_browser=False, wait_time=0, use_dom=False):
        traversed.append(handle)
        return ([], [], [], None)

    monkeypatch.setattr(Tree, "get_nodes", fake_get_nodes)

    return SimpleNamespace(traversed=traversed, uia_attempts=uia_attempts, lookups=lookups)


class TestExcludedProcessNames:
    """Parsing and normalization of `WINDOWS_MCP_EXCLUDE_PROCESSES`."""

    def test_unset_yields_no_exclusions(self):
        assert _excluded_process_names({}) == set()

    def test_blank_entries_are_ignored(self):
        assert _excluded_process_names({"WINDOWS_MCP_EXCLUDE_PROCESSES": "  ,  ,"}) == set()

    def test_strips_whitespace_and_casefolds(self):
        parsed = _excluded_process_names(
            {"WINDOWS_MCP_EXCLUDE_PROCESSES": " Code.exe , CURSOR.EXE ,, "}
        )

        assert parsed == {"code.exe", "cursor.exe"}

    def test_duplicate_names_are_harmless(self):
        parsed = _excluded_process_names(
            {"WINDOWS_MCP_EXCLUDE_PROCESSES": "Code.exe,code.exe,CODE.EXE"}
        )

        assert parsed == {"code.exe"}


class TestWindowProcessName:
    """HWND -> process name is resolved without going through UI Automation."""

    def test_returns_the_owning_process_name(self, monkeypatch):
        monkeypatch.setattr(
            "windows_mcp.tree.service.win32process.GetWindowThreadProcessId",
            lambda handle: (1234, 4242),
        )
        process = MagicMock()
        process.name.return_value = "Code.exe"
        monkeypatch.setattr("windows_mcp.tree.service.Process", lambda pid: process)

        assert _window_process_name(100) == "Code.exe"

    def test_returns_none_without_a_pid(self, monkeypatch):
        monkeypatch.setattr(
            "windows_mcp.tree.service.win32process.GetWindowThreadProcessId",
            lambda handle: (1234, 0),
        )

        assert _window_process_name(100) is None

    def test_returns_none_when_the_lookup_raises(self, monkeypatch):
        def explode(handle):
            raise OSError("process is gone")

        monkeypatch.setattr(
            "windows_mcp.tree.service.win32process.GetWindowThreadProcessId", explode
        )

        assert _window_process_name(100) is None


class TestProcessExclusion:
    """`WINDOWS_MCP_EXCLUDE_PROCESSES` keeps UIA out of the listed processes (#383)."""

    def test_unset_env_traverses_every_window_unchanged(self, tree_instance, monkeypatch):
        recorded = _traversal_recorder(
            tree_instance,
            monkeypatch,
            process_names={100: "Code.exe", 200: "notepad.exe"},
            excluded=None,
        )

        tree_instance.get_window_wise_nodes(windows_handles=[100, 200], active_window_flag=False)

        assert recorded.traversed == [100, 200]
        assert recorded.uia_attempts == [100, 200]
        # Unset means no process lookup is attempted at all — no added per-window cost.
        assert recorded.lookups == []

    def test_excluded_process_is_skipped(self, tree_instance, monkeypatch):
        recorded = _traversal_recorder(
            tree_instance,
            monkeypatch,
            process_names={100: "Code.exe"},
            excluded="Code.exe",
            forbidden_uia={100},
        )

        tree_instance.get_window_wise_nodes(windows_handles=[100], active_window_flag=False)

        assert recorded.traversed == []
        assert recorded.uia_attempts == []

    def test_exclusion_happens_before_uia_is_touched(self, tree_instance, monkeypatch):
        """The key regression test for the safety requirement.

        `ControlFromHandle` is the entry point into a window's accessibility
        provider, so it raises for the excluded HWND. Passing this test proves that
        provider was never entered; a guard placed after the call would fail here.
        """
        recorded = _traversal_recorder(
            tree_instance,
            monkeypatch,
            process_names={100: "Code.exe", 200: "notepad.exe"},
            excluded="Code.exe",
            forbidden_uia={100},
        )

        tree_instance.get_window_wise_nodes(windows_handles=[100, 200], active_window_flag=False)

        assert recorded.uia_attempts == [200]

    def test_mixed_windows_traverse_only_the_allowed_one(self, tree_instance, monkeypatch):
        recorded = _traversal_recorder(
            tree_instance,
            monkeypatch,
            process_names={100: "Code.exe", 200: "notepad.exe"},
            excluded="Code.exe",
            forbidden_uia={100},
        )

        tree_instance.get_window_wise_nodes(windows_handles=[100, 200], active_window_flag=False)

        assert recorded.traversed == [200]

    def test_matching_is_case_insensitive_and_whitespace_tolerant(self, tree_instance, monkeypatch):
        recorded = _traversal_recorder(
            tree_instance,
            monkeypatch,
            process_names={100: "Code.exe", 200: "Cursor.exe", 300: "notepad.exe"},
            excluded=" Code.exe , CURSOR.EXE ,, ",
            forbidden_uia={100, 200},
        )

        tree_instance.get_window_wise_nodes(
            windows_handles=[100, 200, 300], active_window_flag=False
        )

        assert recorded.traversed == [300]

    def test_matching_is_exact_basename_only(self, tree_instance, monkeypatch):
        """No wildcards and no substring matching."""
        recorded = _traversal_recorder(
            tree_instance,
            monkeypatch,
            process_names={100: "Code.exe.bak", 200: "NotCode.exe"},
            excluded="Code.exe",
        )

        tree_instance.get_window_wise_nodes(windows_handles=[100, 200], active_window_flag=False)

        assert recorded.traversed == [100, 200]

    def test_lookup_failure_fails_open(self, tree_instance, monkeypatch):
        """An unresolvable owner must not silently drop the window."""
        recorded = _traversal_recorder(
            tree_instance,
            monkeypatch,
            process_names={100: None, 200: "notepad.exe"},
            excluded="Code.exe",
        )

        tree_instance.get_window_wise_nodes(windows_handles=[100, 200], active_window_flag=False)

        assert recorded.traversed == [100, 200]
        assert recorded.uia_attempts == [100, 200]

    def test_skipped_window_is_not_reported_as_failed(self, tree_instance, monkeypatch):
        """A skip is not a failure, and it spends none of the retry attempts."""
        attempts = []
        _traversal_recorder(
            tree_instance,
            monkeypatch,
            process_names={100: "Code.exe", 200: "notepad.exe"},
            excluded="Code.exe",
        )
        # Retries sleep between attempts; irrelevant here and slow in a test.
        monkeypatch.setattr("windows_mcp.tree.service.sleep", lambda seconds: None)

        def failing_get_nodes(self, handle, is_browser=False, wait_time=0, use_dom=False):
            attempts.append(handle)
            raise RuntimeError("provider unavailable")

        monkeypatch.setattr(Tree, "get_nodes", failing_get_nodes)

        _, _, _, failed_handles, _ = tree_instance.get_window_wise_nodes(
            windows_handles=[100, 200], active_window_flag=False
        )

        assert failed_handles == [200]
        # Every attempt belongs to the traversed window; the skipped one took none.
        assert attempts == [200] * (THREAD_MAX_RETRIES + 1)

    def test_skipped_window_consumes_no_element_budget(self, tree_instance, monkeypatch):
        traversed = []
        tree_instance.element_budget = TreeElementBudget(limit=10)
        _traversal_recorder(
            tree_instance,
            monkeypatch,
            process_names={100: "Code.exe", 200: "notepad.exe"},
            excluded="Code.exe",
        )

        def budgeting_get_nodes(self, handle, is_browser=False, wait_time=0, use_dom=False):
            traversed.append(handle)
            self.element_budget.try_consume(1)
            return ([], [], [], None)

        monkeypatch.setattr(Tree, "get_nodes", budgeting_get_nodes)

        tree_instance.get_window_wise_nodes(windows_handles=[100, 200], active_window_flag=False)

        assert traversed == [200]
        assert tree_instance.element_budget.count == 1
