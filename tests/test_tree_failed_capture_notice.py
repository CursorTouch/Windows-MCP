"""Snapshot and WaitFor must report windows whose UI Automation capture failed (#623).

When a window times out or exhausts retries during tree capture, Tree.get_window_wise_nodes
collects its handle in failed_handles and skips it so the rest of the desktop is still
returned. Previously failed_handles was only logged on the server: Snapshot omitted the
window's subtree without notice, and WaitFor timed out without mentioning that a window's
accessibility capture failed.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import win32gui
from _ctypes import COMError
from fastmcp import Client, FastMCP

from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import DesktopState, Size, Status, Window
from windows_mcp.tools import snapshot
from windows_mcp.tools._snapshot_helpers import build_snapshot_response, capture_desktop_state
from windows_mcp.tools.input import register as register_input_tools
from windows_mcp.tree import service as tree_service
from windows_mcp.tree.service import Tree
from windows_mcp.tree.views import BoundingBox, SemanticNode, TreeElementNode, TreeState
from windows_mcp.uia import UIAError, UIATimeoutError

FROZEN_HANDLE = 257
NOTEPAD_HANDLE = 514
DIALOG_HANDLE = 771
ERROR_TIMEOUT = -2147023436  # 0x800705B4
SCREEN = BoundingBox(left=0, top=0, right=1920, bottom=1080, width=1920, height=1080)


def _box(left: int, top: int, right: int, bottom: int) -> BoundingBox:
    return BoundingBox(
        left=left, top=top, right=right, bottom=bottom, width=right - left, height=bottom - top
    )


def _window(name: str, handle: int, box: BoundingBox, depth: int = 0) -> Window:
    return Window(
        name=name,
        is_browser=False,
        depth=depth,
        status=Status.NORMAL,
        bounding_box=box,
        handle=handle,
        process_id=1000 + depth,
    )


def _save_button() -> tuple[TreeElementNode, SemanticNode]:
    box = _box(1200, 120, 1300, 160)
    element = TreeElementNode(
        name="Save",
        control_type="Button",
        window_name="Untitled - Notepad",
        bounding_box=box,
        center=box.get_center(),
        metadata={},
    )
    win_sem = SemanticNode(
        control_type="Window",
        element_type="window",
        name="Untitled - Notepad",
        window_name="Untitled - Notepad",
    )
    win_sem.add_child(
        SemanticNode(
            control_type="Button",
            element_type="interactive",
            name="Save",
            window_name="Untitled - Notepad",
            center=element.center,
            bounding_box=box,
            metadata={},
            element=element,
        )
    )
    return element, win_sem


def _make_desktop(
    *,
    controls_handles: list[int],
    windows: list[Window],
    active_window: Window | None,
) -> Desktop:
    with patch.object(Desktop, "__init__", lambda self: None):
        desktop = Desktop()
    desktop.desktop_state = None
    desktop.get_screen_size = MagicMock(return_value=Size(width=1920, height=1080))
    desktop.get_screen_box = MagicMock(return_value=SCREEN)
    desktop.is_window_browser = MagicMock(return_value=False)
    desktop.get_displays = MagicMock(return_value=[])
    desktop.get_controls_handles = MagicMock(return_value=controls_handles)
    desktop.get_windows = MagicMock(
        return_value=(list(windows), {window.handle for window in windows})
    )
    desktop.get_active_window = MagicMock(return_value=active_window)
    desktop.get_cursor_location = MagicMock(return_value=(100, 100))
    desktop.tree = Tree(desktop)
    return desktop


def test_snapshot_reports_timed_out_active_window_while_keeping_rest_of_tree(monkeypatch) -> None:
    monkeypatch.delenv("WINDOWS_MCP_EXCLUDE_PROCESSES", raising=False)
    frozen = _window("Frozen Electron\nApp", FROZEN_HANDLE, _box(0, 0, 960, 1080), depth=0)
    notepad = _window("Untitled - Notepad", NOTEPAD_HANDLE, _box(960, 0, 1920, 1080), depth=1)
    # Include a non-app dialog handle that succeeds with an element so we verify partial desktop output.
    desktop = _make_desktop(
        controls_handles=[FROZEN_HANDLE, NOTEPAD_HANDLE, DIALOG_HANDLE],
        windows=[frozen, notepad],
        active_window=frozen,
    )
    button, win_sem = _save_button()

    def get_nodes(self, handle, is_browser=False, wait_time=0, use_dom=False):
        if handle == FROZEN_HANDLE:
            raise UIATimeoutError(ERROR_TIMEOUT)
        if handle == DIALOG_HANDLE:
            return [button], [], [], win_sem
        return [], [], [], None

    with (
        patch.object(
            tree_service,
            "ControlFromHandle",
            side_effect=lambda h: SimpleNamespace(ClassName="Window", Name="Window"),
        ),
        patch.object(Tree, "get_nodes", get_nodes),
        patch(
            "windows_mcp.desktop.service.get_current_desktop",
            return_value={"name": "Desktop 1"},
        ),
        patch(
            "windows_mcp.desktop.service.get_all_desktops",
            return_value=[{"name": "Desktop 1"}],
        ),
    ):
        capture = capture_desktop_state(
            desktop,
            use_vision=False,
            use_dom=False,
            use_annotation=False,
            use_ui_tree=True,
            width_reference_line=None,
            height_reference_line=None,
            display=None,
            region=None,
            tool_name="Snapshot tool",
        )

    state = capture["desktop_state"]
    assert state.tree_state.status is False
    assert state.tree_state.failed_handles == [FROZEN_HANDLE]

    text = build_snapshot_response(capture, include_ui_details=True)[0]
    assert '[#0] (1250,140) button "Save"' in text
    assert '1 window failed UI Automation capture ("Frozen Electron\\nApp" (handle 257))' in text


def test_unlisted_timed_out_handle_is_reported_by_handle_without_extra_uia_lookup(
    monkeypatch,
) -> None:
    monkeypatch.delenv("WINDOWS_MCP_EXCLUDE_PROCESSES", raising=False)
    notepad = _window("Untitled - Notepad", NOTEPAD_HANDLE, _box(960, 0, 1920, 1080))
    # FROZEN_HANDLE timed out during get_windows() too, so it is only in controls_handles.
    desktop = _make_desktop(
        controls_handles=[FROZEN_HANDLE, NOTEPAD_HANDLE],
        windows=[notepad],
        active_window=notepad,
    )
    lookups: list[int] = []

    def control_from_handle(handle: int):
        lookups.append(handle)
        if handle == FROZEN_HANDLE:
            raise UIATimeoutError(ERROR_TIMEOUT)
        return SimpleNamespace(ClassName="Notepad", Name="Untitled - Notepad")

    button, win_sem = _save_button()

    def get_nodes(self, handle, is_browser=False, wait_time=0, use_dom=False):
        return [button], [], [], win_sem

    with (
        patch.object(tree_service, "ControlFromHandle", side_effect=control_from_handle),
        patch.object(Tree, "get_nodes", get_nodes),
        patch(
            "windows_mcp.desktop.service.get_current_desktop",
            return_value={"name": "Desktop 1"},
        ),
        patch(
            "windows_mcp.desktop.service.get_all_desktops",
            return_value=[{"name": "Desktop 1"}],
        ),
    ):
        capture = capture_desktop_state(
            desktop,
            use_vision=False,
            use_dom=False,
            use_annotation=False,
            use_ui_tree=True,
            width_reference_line=None,
            height_reference_line=None,
            display=None,
            region=None,
            tool_name="Snapshot tool",
        )

    # Only one lookup on the hung handle — never re-queried for its Name.
    assert lookups.count(FROZEN_HANDLE) == 1
    text = build_snapshot_response(capture, include_ui_details=True)[0]
    assert "1 window failed UI Automation capture (handle 257)" in text


def test_closed_window_is_not_reported_as_failed_capture(monkeypatch) -> None:
    monkeypatch.delenv("WINDOWS_MCP_EXCLUDE_PROCESSES", raising=False)
    notepad = _window("Untitled - Notepad", NOTEPAD_HANDLE, _box(960, 0, 1920, 1080))
    desktop = _make_desktop(
        controls_handles=[DIALOG_HANDLE, NOTEPAD_HANDLE],
        windows=[notepad],
        active_window=notepad,
    )
    button, win_sem = _save_button()

    def control_from_handle(handle: int):
        if handle == DIALOG_HANDLE:
            raise COMError(
                UIAError.UIA_E_ELEMENTNOTAVAILABLE,
                "Element not available",
                (None, None, None, 0, None),
            )
        return SimpleNamespace(ClassName="Notepad", Name="Untitled - Notepad")

    with (
        patch.object(tree_service, "ControlFromHandle", side_effect=control_from_handle),
        patch.object(win32gui, "IsWindow", side_effect=lambda h: h != DIALOG_HANDLE),
        patch.object(Tree, "get_nodes", lambda self, *a, **kw: ([button], [], [], win_sem)),
        patch(
            "windows_mcp.desktop.service.get_current_desktop",
            return_value={"name": "Desktop 1"},
        ),
        patch(
            "windows_mcp.desktop.service.get_all_desktops",
            return_value=[{"name": "Desktop 1"}],
        ),
    ):
        capture = capture_desktop_state(
            desktop,
            use_vision=False,
            use_dom=False,
            use_annotation=False,
            use_ui_tree=True,
            width_reference_line=None,
            height_reference_line=None,
            display=None,
            region=None,
            tool_name="Snapshot tool",
        )

    assert capture["desktop_state"].tree_state.failed_handles == []
    text = build_snapshot_response(capture, include_ui_details=True)[0]
    assert "failed UI Automation capture" not in text


def test_region_filter_and_empty_semantic_root_preserve_capture_notices() -> None:
    with patch.object(Desktop, "__init__", lambda self: None):
        desktop = Desktop()

    region = _box(960, 0, 1920, 1080)
    root = SemanticNode(control_type="Desktop", element_type="desktop", name="Desktop")
    tree_state = TreeState(
        status=False,
        semantic_tree_root=root,
        truncated=True,
        element_limit=500,
        failed_handles=[FROZEN_HANDLE, DIALOG_HANDLE],
        failed_windows={FROZEN_HANDLE: "Frozen App"},
    )

    filtered = desktop._filter_tree_state_to_region(tree_state, region)
    rendered = filtered.semantic_tree_to_string()
    assert "500-element capture limit" in rendered
    assert (
        '2 windows failed UI Automation capture ("Frozen App" (handle 257), handle 771)' in rendered
    )

    empty_root_state = TreeState(
        status=False,
        semantic_tree_root=None,
        truncated=True,
        element_limit=100,
        failed_handles=[FROZEN_HANDLE],
    )
    empty_rendered = empty_root_state.semantic_tree_to_string()
    assert empty_rendered.startswith("No elements")
    assert "100-element capture limit" in empty_rendered
    assert "1 window failed UI Automation capture (handle 257)" in empty_rendered


@pytest.mark.asyncio
async def test_snapshot_mcp_tool_surfaces_failed_capture_notice(monkeypatch) -> None:
    monkeypatch.delenv("WINDOWS_MCP_EXCLUDE_PROCESSES", raising=False)
    monkeypatch.setattr(snapshot, "state_tool", snapshot.state_tool)
    monkeypatch.setattr(snapshot, "screenshot_tool", snapshot.screenshot_tool)

    frozen = _window("Hung Browser", FROZEN_HANDLE, _box(0, 0, 1920, 1080))
    desktop = _make_desktop(
        controls_handles=[FROZEN_HANDLE],
        windows=[frozen],
        active_window=frozen,
    )
    mcp = FastMCP("failed-capture")
    snapshot.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)

    with (
        patch.object(
            tree_service,
            "ControlFromHandle",
            return_value=SimpleNamespace(ClassName="Chrome_WidgetWin_1", Name="Hung Browser"),
        ),
        patch.object(Tree, "get_nodes", side_effect=UIATimeoutError(ERROR_TIMEOUT)),
        patch(
            "windows_mcp.desktop.service.get_current_desktop",
            return_value={"name": "Desktop 1"},
        ),
        patch(
            "windows_mcp.desktop.service.get_all_desktops",
            return_value=[{"name": "Desktop 1"}],
        ),
    ):
        async with Client(mcp) as client:
            result = await client.call_tool("Snapshot", {}, raise_on_error=False)

    assert not result.is_error
    payload = json.loads(result.content[0].text)[0]
    assert '1 window failed UI Automation capture ("Hung Browser" (handle 257))' in payload


def test_wait_for_timeout_mentions_failed_window_captures() -> None:
    class FakeMCP:
        def __init__(self) -> None:
            self.tools = {}

        def tool(self, *, name: str, **kwargs):
            def decorator(func):
                self.tools[name] = func
                return func

            return decorator

    state = DesktopState(
        active_desktop={"name": "Desktop 1"},
        all_desktops=[],
        active_window=_window("Checkout", FROZEN_HANDLE, _box(0, 0, 800, 600)),
        windows=[],
        tree_state=TreeState(
            status=False,
            failed_handles=[FROZEN_HANDLE],
            failed_windows={FROZEN_HANDLE: "Checkout\ud83d"},
        ),
    )

    class FakeDesktop:
        desktop_state = state

        def get_state(self, **kwargs):
            return state

    mcp = FakeMCP()
    register_input_tools(mcp, get_desktop=lambda: FakeDesktop(), get_analytics=lambda: None)

    with pytest.raises(TimeoutError) as exc_info:
        asyncio.run(
            mcp.tools["WaitFor"](
                condition="element_exists",
                text="Submit",
                window_name="Checkout",
                timeout=0.01,
                interval=0.001,
            )
        )

    message = str(exc_info.value)
    message.encode("utf-8")  # Surrogates in window titles must be repaired.
    assert "1 window failed UI Automation capture" in message
    assert "handle 257" in message
