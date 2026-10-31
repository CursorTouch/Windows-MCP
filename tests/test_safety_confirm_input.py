"""Input that answers a safety confirmation dialog must not count as user takeover.

The desktop control coordinator is loaded with its Win32 hook modules stubbed, so the
state machine runs on any OS.
"""

import importlib
import sys
import threading
import time
import types

import pytest


@pytest.fixture
def control(monkeypatch):
    win32 = types.ModuleType("windows_mcp.desktop.control_win32")
    win32._MouseHookData = win32._KeyHookData = object
    win32._user32 = None
    win32._key = lambda vk: vk
    win32._interactive_desktop = lambda: True
    win32.run_input_monitor = lambda owner: None
    hooks = types.ModuleType("windows_mcp.desktop.control_hooks")
    monkeypatch.setitem(sys.modules, "windows_mcp.desktop.control_win32", win32)
    monkeypatch.setitem(sys.modules, "windows_mcp.desktop.control_hooks", hooks)
    monkeypatch.delitem(sys.modules, "windows_mcp.desktop.control", raising=False)
    import windows_mcp.desktop as desktop_pkg

    monkeypatch.setattr(desktop_pkg, "control_hooks", hooks, raising=False)
    module = importlib.import_module("windows_mcp.desktop.control")
    yield module
    sys.modules.pop("windows_mcp.desktop.control", None)


def ready(control):
    owner = control.ControlCoordinator()
    with owner._lock:
        owner._set_locked("ready")
    return owner


def physical_click(owner):
    # What the mouse hook does: stamp first, then queue the event.
    owner._last_physical_event = time.monotonic()
    owner._handle(("point", 10, 10, 0x0202))


def test_dialog_click_is_excused_and_ai_can_proceed(control):
    owner = ready(control)
    begin = owner.begin_confirmation()
    assert begin[1] is False
    physical_click(owner)  # Clicking "Yes".
    assert owner.status()["state"] == "user"  # Old behaviour: USER_CONTROL.
    owner.end_confirmation(*begin)
    assert owner.status()["state"] == "ready"
    token = owner.begin_call("Shortcut")  # No ControlBlocked("USER_CONTROL").
    owner.end_call(token)


def test_queued_dialog_events_after_approval_are_still_excused(control):
    owner = ready(control)
    begin = owner.begin_confirmation()
    owner._last_physical_event = time.monotonic()  # Stamped during the dialog...
    owner.end_confirmation(*begin)
    owner._handle(("point", 10, 10, 0x0202))  # ...but handled only now.
    assert owner.status()["state"] == "ready"


def test_input_after_approval_still_takes_over(control):
    owner = ready(control)
    begin = owner.begin_confirmation()
    physical_click(owner)
    owner.end_confirmation(*begin)
    time.sleep(0.01)
    physical_click(owner)  # Real user input after the dialog closed.
    assert owner.status()["state"] == "user"
    with pytest.raises(control.ControlBlocked, match="USER_CONTROL"):
        owner.begin_call("Shortcut")


def test_user_active_before_dialog_is_not_excused(control):
    owner = ready(control)
    physical_click(owner)  # The user was already working.
    begin = owner.begin_confirmation()
    assert begin[1] is True
    physical_click(owner)
    owner.end_confirmation(*begin)
    assert owner.status()["state"] == "user"


def test_enter_key_release_after_dialog_is_waited_for(control):
    owner = ready(control)
    begin = owner.begin_confirmation()
    owner._pressed.add(0x0D)  # Enter answered the dialog on key-down.
    owner._last_physical_event = time.monotonic()
    owner._handle(("key",))

    def release():
        time.sleep(0.05)
        owner._last_physical_event = time.monotonic()
        owner._pressed.discard(0x0D)
        owner._handle(("key",))

    threading.Thread(target=release).start()
    owner.end_confirmation(*begin)
    assert owner.status()["state"] == "ready"


def test_key_still_held_after_timeout_is_not_excused(control):
    owner = ready(control)
    begin = owner.begin_confirmation()
    owner._pressed.add(0x41)
    physical_click(owner)
    owner.end_confirmation(*begin, release_timeout=0.05)
    assert owner._excused is None and owner.status()["state"] == "user"


async def test_gate_excuses_input_only_after_approval(tmp_path):
    from fastmcp import Client, FastMCP

    from windows_mcp.safety.config import parse_safety_config
    from windows_mcp.safety.gate import SafetyGate

    class Monitor:
        def __init__(self):
            self.calls = []

        def begin_confirmation(self):
            self.calls.append("begin")
            return (1.0, False)

        def end_confirmation(self, start, active):
            self.calls.append(("end", start, active))

    class Platform:
        own_pid = None
        answer = True

        def process_name(self, pid):
            return "notepad.exe"

        def foreground_pid(self):
            return 1

        def pid_at_point(self, x, y):
            return 1

        def confirm_dialog(self, title, text, timeout):
            return self.answer

    cfg = parse_safety_config(
        {
            "audit": {"path": str(tmp_path / "a.jsonl")},
            "kill_switch": {"flag_file": str(tmp_path / "K")},
        }
    )
    platform, monitor = Platform(), Monitor()
    mcp = FastMCP("t")
    mcp.add_middleware(SafetyGate(cfg, platform, input_monitor=monitor))

    @mcp.tool(name="Shortcut")
    def shortcut(shortcut: str) -> str:
        return "sent"

    async with Client(mcp) as c:
        assert (await c.call_tool("Shortcut", {"shortcut": "delete"})).data == "sent"
        assert monitor.calls == ["begin", ("end", 1.0, False)]
        platform.answer = False
        with pytest.raises(Exception, match="safety_declined"):
            await c.call_tool("Shortcut", {"shortcut": "delete"})
    assert monitor.calls == ["begin", ("end", 1.0, False), "begin"]
