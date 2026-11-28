"""The windows of the program that launched the server stay out of UI Automation (#383)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import windows_mcp.uia as uia
from windows_mcp.desktop.service import Desktop
from windows_mcp.tree import host_process
from windows_mcp.tree.service import Tree
from windows_mcp.tree.views import BoundingBox, TreeState

HOST = "windows_mcp.tree.host_process"
SERVICE = "windows_mcp.desktop.service"
TREE_SERVICE = "windows_mcp.tree.service"

# Launch chain used by the tests, nearest parent first:
# server <- uv (11) <- client CLI (12) <- editor (13) <- shell (14) <- winlogon (15)
SHELL_PID = 14
CHAIN = [(11, 1.0), (12, 2.0), (13, 3.0), (SHELL_PID, 4.0), (15, 5.0)]


def _fake_process(pid, started):
    return SimpleNamespace(pid=pid, create_time=lambda: started)


@pytest.fixture
def launch_chain(monkeypatch):
    """Enable the host exclusion and give the server a known launch chain."""
    monkeypatch.setenv("WINDOWS_MCP_EXCLUDE_HOST_PROCESS", "1")
    started = dict(CHAIN)

    def process(pid=None):
        if pid is None:
            return SimpleNamespace(parents=lambda: [_fake_process(p, s) for p, s in CHAIN])
        if pid not in started:
            raise ProcessLookupError(pid)
        return _fake_process(pid, started[pid])

    monkeypatch.setattr(f"{HOST}.psutil.Process", process)
    monkeypatch.setattr(f"{HOST}._shell_process_id", lambda: SHELL_PID)
    # In these tests a window handle is its owner's PID plus 1000.
    monkeypatch.setattr(
        f"{HOST}.win32process.GetWindowThreadProcessId", lambda handle: (1, handle - 1000)
    )
    host_process.host_processes.cache_clear()
    yield started
    host_process.host_processes.cache_clear()


class TestHostProcesses:
    def test_chain_stops_before_the_desktop_shell(self, launch_chain):
        # The shell owns the taskbar and the desktop: it and everything above it
        # must stay reachable.
        assert host_process.host_processes() == {11: 1.0, 12: 2.0, 13: 3.0}

    def test_whole_chain_is_kept_when_there_is_no_shell_window(self, launch_chain, monkeypatch):
        monkeypatch.setattr(f"{HOST}._shell_process_id", lambda: 0)
        host_process.host_processes.cache_clear()
        assert set(host_process.host_processes()) == {11, 12, 13, 14, 15}

    def test_unreadable_chain_yields_no_hosts(self, launch_chain, monkeypatch):
        def process(pid=None):
            raise OSError("access denied")

        monkeypatch.setattr(f"{HOST}.psutil.Process", process)
        host_process.host_processes.cache_clear()
        assert host_process.host_processes() == {}


class TestIsHostWindow:
    def test_window_of_a_launching_process_is_a_host_window(self, launch_chain):
        assert host_process.is_host_window(1013) is True

    def test_window_of_another_process_is_not(self, launch_chain):
        assert host_process.is_host_window(1099) is False

    def test_shell_window_is_not_a_host_window(self, launch_chain):
        assert host_process.is_host_window(1000 + SHELL_PID) is False

    def test_reused_pid_is_not_a_host_window(self, launch_chain):
        # The launcher exited and Windows gave its PID to an unrelated program.
        launch_chain[13] = 99.0
        assert host_process.is_host_window(1013) is False

    def test_unresolvable_owner_is_not_a_host_window(self, launch_chain, monkeypatch):
        def no_owner(handle):
            raise OSError("invalid window handle")

        monkeypatch.setattr(f"{HOST}.win32process.GetWindowThreadProcessId", no_owner)
        assert host_process.is_host_window(1013) is False

    @pytest.mark.parametrize("value", ["0", "false", "No", " OFF "])
    def test_opt_out_disables_the_check(self, launch_chain, monkeypatch, value):
        monkeypatch.setenv("WINDOWS_MCP_EXCLUDE_HOST_PROCESS", value)
        with patch(f"{HOST}.win32process.GetWindowThreadProcessId") as owner_lookup:
            assert host_process.is_host_window(1013) is False
        owner_lookup.assert_not_called()

    @pytest.mark.parametrize("value", [None, "", "1", "true", "anything"])
    def test_enabled_unless_switched_off(self, value):
        env = {} if value is None else {"WINDOWS_MCP_EXCLUDE_HOST_PROCESS": value}
        assert host_process.host_exclusion_enabled(env) is True


def test_tree_walk_never_enters_a_host_window(launch_chain, monkeypatch):
    desktop = MagicMock()
    desktop.is_window_browser.return_value = False
    tree = MagicMock()
    tree.desktop = desktop
    tree.element_budget = SimpleNamespace(exhausted=False)
    monkeypatch.delenv("WINDOWS_MCP_EXCLUDE_PROCESSES", raising=False)

    uia_attempts = []

    def control_from_handle(handle):
        uia_attempts.append(handle)
        return MagicMock(ClassName="SomeWindow", Name="Some Window")

    monkeypatch.setattr(f"{TREE_SERVICE}.ControlFromHandle", control_from_handle)
    traversed = []
    tree.get_nodes.side_effect = lambda handle, is_browser, use_dom=False: (
        traversed.append(handle) or ([], [], [], None)
    )

    result = Tree.get_window_wise_nodes(tree, [1013, 1099], False)

    assert uia_attempts == [1099]
    assert traversed == [1099]
    # A window skipped on purpose is not a failed capture.
    assert result[3] == []


@pytest.mark.parametrize("foreground", [1013, 1099], ids=["host-in-front", "other-in-front"])
def test_snapshot_omits_host_windows_before_metadata(launch_chain, monkeypatch, foreground):
    monkeypatch.delenv("WINDOWS_MCP_EXCLUDE_PROCESSES", raising=False)
    controls = {}
    for handle in (1013, 1099):
        control = MagicMock(spec=uia.WindowControl)
        control.Name = f"Window {handle}"
        control.ClassName = "FakeWindowClass"
        control.NativeWindowHandle = handle
        control.ProcessId = handle - 1000
        control.BoundingRectangle = uia.Rect(0, 0, 800, 600)
        control.GetPattern.return_value = SimpleNamespace(CanMinimize=True, CanMaximize=True)
        control.GetParentControl.return_value = None
        controls[handle] = control

    uia_attempts = []

    def control_from_handle(handle):
        uia_attempts.append(handle)
        return controls[handle]

    with (
        patch.object(Desktop, "__init__", lambda self: None),
        patch.object(Desktop, "get_controls_handles", return_value=[1013, 1099]),
        patch.object(Desktop, "get_displays", return_value=[]),
        patch.object(Desktop, "get_cursor_location", return_value=(0, 0)),
        patch.object(Desktop, "is_window_browser", return_value=False),
        patch(f"{SERVICE}.get_current_desktop", return_value={"name": "Desktop 1"}),
        patch(f"{SERVICE}.get_all_desktops", return_value=[{"name": "Desktop 1"}]),
        patch(f"{SERVICE}.sleep"),
        patch(f"{SERVICE}.uia.ControlFromHandle", side_effect=control_from_handle),
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
        state = desktop.get_state(use_vision=False, use_ui_tree=True)

    assert 1013 not in uia_attempts
    listed = state.windows + ([state.active_window] if state.active_window else [])
    assert [window.handle for window in listed] == [1099]
    assert (state.active_window.handle if state.active_window else None) == (
        1099 if foreground == 1099 else None
    )
