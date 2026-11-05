"""Snapshot must exclude providers before collecting window metadata."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import windows_mcp.uia as uia
from windows_mcp.desktop.service import Desktop
from windows_mcp.tree.service import Tree
from windows_mcp.tree.views import BoundingBox, TreeState

SERVICE = "windows_mcp.desktop.service"
TREE_SERVICE = "windows_mcp.tree.service"


@pytest.mark.parametrize(
    "excluded, foreground, process_name, expected, use_ui_tree",
    [
        (" Code.EXE ,, ", 200, "code.exe", [200], True),
        ("code.exe", 100, "Code.exe", [200], True),
        ("code.exe,notepad.exe", 100, "Code.exe", [], True),
        ("code.exe", 200, "Code.exe.helper", [100, 200], True),
        (None, 200, "Code.exe", [100, 200], True),
        (" , ", 200, "Code.exe", [100, 200], True),
        ("code.exe", 100, None, [100, 200], True),
        ("code.exe", 0, "Code.exe", [200], True),
        ("code.exe", 100, "vanished.exe", [200], True),
        ("code.exe", 100, "Code.exe", [], False),
    ],
    ids=[
        "excluded-background",
        "excluded-foreground",
        "all-excluded",
        "exact-basename",
        "unset",
        "blank",
        "unknown-owner",
        "null-foreground",
        "vanished",
        "no-ui-tree",
    ],
)
def test_snapshot_excludes_processes_before_metadata(
    monkeypatch, excluded, foreground, process_name, expected, use_ui_tree
):
    if excluded is None:
        monkeypatch.delenv("WINDOWS_MCP_EXCLUDE_PROCESSES", raising=False)
    else:
        monkeypatch.setenv("WINDOWS_MCP_EXCLUDE_PROCESSES", excluded)

    names = {100: process_name, 200: "notepad.exe"}
    forbidden = {
        handle
        for handle, name in names.items()
        if name
        and name.casefold() in {part.strip().casefold() for part in (excluded or "").split(",")}
    }
    controls = {}
    for handle in names:
        control = MagicMock(spec=uia.WindowControl)
        control.Name = f"Window {handle}"
        control.ClassName = "FakeWindowClass"
        control.NativeWindowHandle = handle
        control.ProcessId = handle + 1000
        control.BoundingRectangle = uia.Rect(0, 0, 800, 600)
        control.GetPattern.return_value = SimpleNamespace(CanMinimize=True, CanMaximize=True)
        control.GetParentControl.return_value = None
        controls[handle] = control

    uia_attempts = []

    def control_from_handle(handle):
        # Record before raising: metadata handlers may swallow the exception.
        uia_attempts.append(handle)
        if handle in forbidden or names[handle] == "vanished.exe":
            raise RuntimeError("Provider unavailable")
        return controls[handle]

    def process(pid):
        if names[pid - 1000] is None:
            raise ProcessLookupError(pid)
        return SimpleNamespace(name=lambda: names[pid - 1000])

    with (
        patch.object(Desktop, "__init__", lambda self: None),
        patch.object(Desktop, "get_controls_handles", return_value=[100, 200]) as enumerate_windows,
        patch.object(Desktop, "get_displays", return_value=[]),
        patch.object(Desktop, "get_cursor_location", return_value=(0, 0)),
        patch.object(Desktop, "is_window_browser", return_value=False),
        patch(f"{SERVICE}.get_current_desktop", return_value={"name": "Desktop 1"}),
        patch(f"{SERVICE}.get_all_desktops", return_value=[{"name": "Desktop 1"}]),
        patch(f"{SERVICE}.sleep"),
        patch(
            f"{TREE_SERVICE}.win32process.GetWindowThreadProcessId",
            side_effect=lambda handle: (1, handle + 1000),
        ),
        patch(f"{TREE_SERVICE}.Process", side_effect=process) as process_lookup,
        patch(f"{SERVICE}.uia.ControlFromHandle", side_effect=control_from_handle),
        patch(f"{TREE_SERVICE}.ControlFromHandle", side_effect=control_from_handle),
        patch(f"{SERVICE}.uia.GetForegroundWindow", return_value=foreground),
        patch(f"{SERVICE}.uia.GetRootControl", return_value=SimpleNamespace(NativeWindowHandle=1)),
        patch(f"{SERVICE}.uia.IsIconic", return_value=False),
        patch(f"{SERVICE}.uia.IsZoomed", return_value=False),
        patch(f"{SERVICE}.uia.IsWindowVisible", return_value=True),
    ):
        desktop = Desktop()
        desktop.tree = MagicMock()
        desktop.tree.get_state.return_value = TreeState()
        desktop.tree.screen_box = BoundingBox(
            left=0, top=0, right=800, bottom=600, width=800, height=600
        )
        with patch.object(
            desktop,
            "get_window_from_element_handle",
            wraps=desktop.get_window_from_element_handle,
        ) as resolve_foreground:
            state = desktop.get_state(use_vision=False, use_ui_tree=use_ui_tree)

        assert not forbidden.intersection(uia_attempts)
        assert not forbidden.intersection(
            call.args[0] for call in resolve_foreground.call_args_list
        )
        metadata = state.windows + ([state.active_window] if state.active_window else [])
        assert sorted((window.handle, window.depth) for window in metadata) == [
            (handle, [100, 200].index(handle)) for handle in expected
        ]
        assert all(window.name == f"Window {window.handle}" for window in metadata)
        assert (state.active_window.handle if state.active_window else None) == (
            foreground if foreground in expected else None
        )
        if use_ui_tree:
            assert state.tree_state is desktop.tree.get_state.return_value
        else:
            enumerate_windows.assert_not_called()
            desktop.tree.get_state.assert_not_called()
            assert uia_attempts == []
        if excluded is None or not excluded.replace(",", "").strip() or not use_ui_tree:
            process_lookup.assert_not_called()

        if forbidden == {100, 200}:
            # Intentionally skipped windows are not failed tree captures.
            assert Tree.get_window_wise_nodes(desktop.tree, [100, 200], False) == (
                [],
                [],
                [],
                [],
                [],
            )
            assert uia_attempts == []

        enumerate_windows.reset_mock()
        assert desktop.get_windows(controls_handles=[]) == ([], set())
        enumerate_windows.assert_not_called()
