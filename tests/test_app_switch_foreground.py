from unittest.mock import Mock
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.desktop import service, window as window_module
from windows_mcp.desktop.control import ControlBlocked
from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.window import ExactWindowController
from windows_mcp.tools import app


@pytest.fixture
def switch_desktop(monkeypatch, sample_window):
    desktop = Desktop.__new__(Desktop)
    desktop._find_window_by_name = Mock(return_value=(sample_window, ""))
    desktop.bring_window_to_top = Mock()
    monkeypatch.setattr(service.uia, "IsIconic", lambda _: False)
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", lambda: sample_window.handle)

    clock = {"elapsed": 0.0, "sleeps": []}

    def sleep(seconds):
        clock["sleeps"].append(seconds)
        clock["elapsed"] += seconds

    monkeypatch.setattr(service, "perf_counter", lambda: clock["elapsed"])
    monkeypatch.setattr(service, "sleep", sleep)
    return desktop, clock


@pytest.mark.parametrize("minimized", [False, True])
def test_switch_succeeds_only_after_target_is_foreground(
    switch_desktop, sample_window, monkeypatch, minimized
):
    desktop, clock = switch_desktop
    monkeypatch.setattr(service.uia, "IsIconic", lambda _: minimized)

    response, status = desktop.switch_app("Notepad")

    assert status == 0
    assert response == (
        f"Restored {sample_window.name.title()} from minimized and switched to it."
        if minimized
        else f"Switched to {sample_window.name.title()} window."
    )
    desktop.bring_window_to_top.assert_called_once_with(sample_window.handle)
    assert clock["sleeps"] == []


def test_switch_waits_for_delayed_foreground(switch_desktop, sample_window, monkeypatch):
    desktop, clock = switch_desktop
    foreground = Mock(side_effect=[0, 999, sample_window.handle])
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", foreground)

    assert desktop.switch_app("Notepad")[1] == 0
    assert foreground.call_count == 3
    assert 0 < clock["elapsed"] < 1.0


@pytest.mark.parametrize("foreground_handle", [0, 999])
def test_switch_rejects_unchanged_foreground_with_bounded_wait(
    switch_desktop, sample_window, monkeypatch, foreground_handle
):
    desktop, clock = switch_desktop
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", lambda: foreground_handle)

    response, status = desktop.switch_app("Notepad")

    assert status == 1
    assert "Failed to bring" in response
    assert sample_window.name in response
    assert "foreground" in response
    assert 1.0 <= clock["elapsed"] <= 1.05
    desktop.bring_window_to_top.assert_called_once_with(sample_window.handle)


def test_switch_does_not_activate_a_missing_window(switch_desktop):
    desktop, clock = switch_desktop
    desktop._find_window_by_name.return_value = (None, "Application not found.")

    assert desktop.switch_app("missing") == ("Application not found.", 1)
    desktop.bring_window_to_top.assert_not_called()
    assert clock["sleeps"] == []


def test_switch_reports_foreground_query_failure(switch_desktop, monkeypatch):
    desktop, _ = switch_desktop
    monkeypatch.setattr(
        service.win32gui, "GetForegroundWindow", Mock(side_effect=RuntimeError("query failed"))
    )

    assert desktop.switch_app("Notepad") == ("Error switching app: query failed", 1)


def test_exact_activation_stops_inside_foreground_helper_after_takeover(monkeypatch):
    state = {"value": "ai"}
    actions = []

    def checkpoint():
        if state["value"] != "ai":
            raise ControlBlocked("CONTROL_PREEMPTED", {"state": state["value"]})

    owner = SimpleNamespace(checkpoint_current=checkpoint, record_step_current=lambda: None)
    monkeypatch.setattr(window_module, "get_controller", lambda: owner)
    monkeypatch.setattr(service.win32gui, "IsWindow", lambda handle: handle == 100)
    monkeypatch.setattr(service.win32gui, "IsIconic", lambda handle: True)
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", lambda: 999)

    def show_then_take_over(*args):
        actions.append("show")
        state["value"] = "user"

    monkeypatch.setattr(service.win32gui, "ShowWindow", show_then_take_over)
    monkeypatch.setattr(
        service.win32gui, "SetForegroundWindow", lambda *args: actions.append("foreground")
    )
    monkeypatch.setattr(service.win32gui, "BringWindowToTop", lambda *args: actions.append("top"))
    monkeypatch.setattr(service.win32gui, "SetWindowPos", lambda *args: actions.append("position"))

    exact = ExactWindowController(Desktop.__new__(Desktop))
    monkeypatch.setattr(exact, "_require_exact_window", lambda *args: {"handle": 100})

    with pytest.raises(ControlBlocked) as exc:
        exact.activate_exact_window(handle=100)

    assert exc.value.code == "CONTROL_PREEMPTED"
    assert actions == ["show"]


def test_exact_activation_rechecks_after_foreground_read(monkeypatch):
    state = {"value": "ai"}
    calls = []

    def checkpoint():
        if state["value"] != "ai":
            raise ControlBlocked("CONTROL_PREEMPTED", {"state": state["value"]})

    def foreground_then_take_over():
        state["value"] = "user"
        return 999

    owner = SimpleNamespace(checkpoint_current=checkpoint, record_step_current=lambda: None)
    desktop = SimpleNamespace(bring_window_to_top=lambda *args, **kwargs: calls.append(args))
    exact = ExactWindowController(desktop)
    monkeypatch.setattr(exact, "_require_exact_window", lambda *args: {"handle": 100})
    monkeypatch.setattr(window_module, "get_controller", lambda: owner)
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", foreground_then_take_over)

    with pytest.raises(ControlBlocked) as exc:
        exact.activate_exact_window(handle=100)

    assert exc.value.code == "CONTROL_PREEMPTED"
    assert calls == []


def test_exact_helper_detaches_after_takeover_during_thread_attach(monkeypatch):
    state = {"value": "ai"}
    actions = []

    def checkpoint():
        if state["value"] != "ai":
            raise ControlBlocked("CONTROL_PREEMPTED", {"state": state["value"]})

    def attach(current, thread, enabled):
        actions.append(("attach" if enabled else "detach", thread))
        if enabled:
            state["value"] = "user"

    monkeypatch.setattr(service.win32gui, "IsWindow", lambda handle: handle in (100, 999))
    monkeypatch.setattr(service.win32gui, "IsIconic", lambda handle: False)
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", lambda: 999)
    monkeypatch.setattr(
        service.win32process,
        "GetWindowThreadProcessId",
        lambda handle: (2 if handle == 999 else 3, 0),
    )
    monkeypatch.setattr(service.win32process, "AttachThreadInput", attach)
    monkeypatch.setattr(
        service.ctypes,
        "windll",
        SimpleNamespace(
            kernel32=SimpleNamespace(GetCurrentThreadId=lambda: 1),
            user32=SimpleNamespace(AllowSetForegroundWindow=lambda value: actions.append("allow")),
        ),
    )
    for action in ("SetForegroundWindow", "BringWindowToTop", "SetWindowPos"):
        monkeypatch.setattr(service.win32gui, action, lambda *args: actions.append("window"))

    with pytest.raises(ControlBlocked) as exc:
        Desktop.__new__(Desktop).bring_window_to_top(100, checkpoint=checkpoint)

    assert exc.value.code == "CONTROL_PREEMPTED"
    assert actions == ["allow", ("attach", 2), ("detach", 2)]


def test_exact_helper_checks_before_allowing_foreground_access(monkeypatch):
    state = {"value": "ai"}
    actions = []

    def checkpoint():
        if state["value"] != "ai":
            raise ControlBlocked("CONTROL_PREEMPTED", {"state": state["value"]})

    def thread_id(handle):
        if handle == 100:
            state["value"] = "user"
        return (2 if handle == 999 else 3, 0)

    monkeypatch.setattr(service.win32gui, "IsWindow", lambda handle: handle in (100, 999))
    monkeypatch.setattr(service.win32gui, "IsIconic", lambda handle: False)
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", lambda: 999)
    monkeypatch.setattr(service.win32process, "GetWindowThreadProcessId", thread_id)
    monkeypatch.setattr(
        service.win32process, "AttachThreadInput", lambda *args: actions.append("attach")
    )
    monkeypatch.setattr(
        service.ctypes,
        "windll",
        SimpleNamespace(
            kernel32=SimpleNamespace(GetCurrentThreadId=lambda: 1),
            user32=SimpleNamespace(AllowSetForegroundWindow=lambda value: actions.append("allow")),
        ),
    )
    for action in ("SetForegroundWindow", "BringWindowToTop", "SetWindowPos"):
        monkeypatch.setattr(service.win32gui, action, lambda *args: actions.append("window"))

    with pytest.raises(ControlBlocked) as exc:
        Desktop.__new__(Desktop).bring_window_to_top(100, checkpoint=checkpoint)

    assert exc.value.code == "CONTROL_PREEMPTED"
    assert actions == []


def test_exact_helper_stops_after_foreground_change_and_detaches(monkeypatch):
    state = {"value": "ai"}
    actions = []

    def checkpoint():
        if state["value"] != "ai":
            raise ControlBlocked("CONTROL_PREEMPTED", {"state": state["value"]})

    def foreground_then_take_over(*args):
        actions.append("foreground")
        state["value"] = "user"

    monkeypatch.setattr(service.win32gui, "IsWindow", lambda handle: handle in (100, 999))
    monkeypatch.setattr(service.win32gui, "IsIconic", lambda handle: False)
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", lambda: 999)
    monkeypatch.setattr(
        service.win32process,
        "GetWindowThreadProcessId",
        lambda handle: (2 if handle == 999 else 3, 0),
    )
    monkeypatch.setattr(
        service.win32process,
        "AttachThreadInput",
        lambda current, thread, enabled: actions.append(
            ("attach" if enabled else "detach", thread)
        ),
    )
    monkeypatch.setattr(
        service.ctypes,
        "windll",
        SimpleNamespace(
            kernel32=SimpleNamespace(GetCurrentThreadId=lambda: 1),
            user32=SimpleNamespace(AllowSetForegroundWindow=lambda value: actions.append("allow")),
        ),
    )
    monkeypatch.setattr(service.win32gui, "SetForegroundWindow", foreground_then_take_over)
    monkeypatch.setattr(service.win32gui, "BringWindowToTop", lambda *args: actions.append("top"))
    monkeypatch.setattr(service.win32gui, "SetWindowPos", lambda *args: actions.append("position"))

    with pytest.raises(ControlBlocked) as exc:
        Desktop.__new__(Desktop).bring_window_to_top(100, checkpoint=checkpoint)

    assert exc.value.code == "CONTROL_PREEMPTED"
    assert actions == [
        "allow", ("attach", 2), ("attach", 3), "foreground", ("detach", 3), ("detach", 2)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("foreground_matches", [False, True])
async def test_app_tool_reports_switch_failure_as_mcp_error(
    switch_desktop, sample_window, monkeypatch, foreground_matches
):
    desktop, _ = switch_desktop
    monkeypatch.setattr(
        service.win32gui,
        "GetForegroundWindow",
        lambda: sample_window.handle if foreground_matches else 999,
    )
    server = FastMCP("switch-foreground-test")
    app.register(server, get_desktop=lambda: desktop, get_analytics=lambda: None)

    async with Client(server) as client:
        result = await client.call_tool(
            "App", {"mode": "switch", "name": "Notepad"}, raise_on_error=False
        )

    assert result.is_error is (not foreground_matches)
    assert ("Switched to" if foreground_matches else "Failed to bring") in result.content[0].text
