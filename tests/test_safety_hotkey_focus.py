"""Kill-switch default hotkey wiring, hook chord detection and dialog focus (Linux-safe)."""

import ctypes
import importlib
import sys
import threading
import types

import pytest

from windows_mcp.infrastructure.config import load_config
from windows_mcp.safety.config import parse_safety_config
from windows_mcp.safety.gate import SafetyGate
from windows_mcp.safety.killswitch import KillSwitch


class _Platform:
    own_pid = 1

    @staticmethod
    def parse_hotkey(spec):
        from windows_mcp.safety.win32 import parse_hotkey

        return parse_hotkey(spec)


class _Factory:
    specs: list = []

    def __init__(self, spec, cb):
        _Factory.specs.append(spec)
        self.cb = cb

    def start(self):
        pass

    def stop(self):
        pass


class _Monitor:
    def __init__(self):
        self.registered = None

    def set_kill_hotkey(self, mods, vk, cb):
        self.registered = (mods, vk, cb)


def test_toml_without_hotkey_keeps_and_registers_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[safety]\nenabled = true\n[safety.kill_switch]\nflag_file = "'
        + str(tmp_path / "KILL").replace("\\", "/")
        + '"\n',
        encoding="utf-8",
    )
    cfg = load_config(path).safety
    assert cfg.kill_switch.hotkey == "ctrl+alt+shift+k"
    _Factory.specs = []
    monitor = _Monitor()
    gate = SafetyGate(
        cfg, _Platform(), kill_switch=KillSwitch(cfg.kill_switch, _Factory), input_monitor=monitor
    )
    gate.start()
    assert _Factory.specs == ["ctrl+alt+shift+k"]  # RegisterHotKey path.
    mods, vk, cb = monitor.registered  # Input-hook path.
    assert (mods, vk) == (0x1 | 0x2 | 0x4, 0x4B)
    cb()
    assert "hotkey" in gate.kill_switch.engaged()


def test_toml_without_safety_table_registers_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[control]\nenabled = true\n", encoding="utf-8")
    cfg = load_config(path).safety
    _Factory.specs = []
    KillSwitch(cfg.kill_switch, _Factory).start()
    assert _Factory.specs == ["ctrl+alt+shift+k"]


def test_disabled_kill_switch_wires_nothing(tmp_path):
    cfg = parse_safety_config({"kill_switch": {"enabled": False}})
    monitor = _Monitor()
    _Factory.specs = []
    SafetyGate(
        cfg, _Platform(), kill_switch=KillSwitch(cfg.kill_switch, _Factory), input_monitor=monitor
    ).start()
    assert monitor.registered is None and _Factory.specs == []


class _KeyData(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_uint32),
        ("scanCode", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


@pytest.fixture
def hooks(monkeypatch):
    win32 = types.ModuleType("windows_mcp.desktop.control_win32")
    win32._CHORD = {0x10, 0x11, 0x12, 0x08}
    win32._KeyHookData = _KeyData
    win32._MouseHookData = _KeyData
    win32._key = lambda vk: {0xA0: 0x10, 0xA2: 0x11, 0xA4: 0x12}.get(vk, vk)
    win32._mouse_button = win32._read_raw_mouse = lambda *a: None
    win32._user32 = types.SimpleNamespace(CallNextHookEx=lambda *a: 0)
    monkeypatch.setitem(sys.modules, "windows_mcp.desktop.control_win32", win32)
    monkeypatch.delitem(sys.modules, "windows_mcp.desktop.control_hooks", raising=False)
    module = importlib.import_module("windows_mcp.desktop.control_hooks")
    yield module
    sys.modules.pop("windows_mcp.desktop.control_hooks", None)


class _Owner:
    def __init__(self, suppress):
        self._key_hook = None
        self._pressed = set()
        self._quarantine = set()
        self._delivered_keys = set()
        self._fast_takeover = False
        self._suppress = suppress
        self._emergency = False
        self._deadline = float("inf")
        self._last_physical_event = 0.0
        self._kill_chord = (frozenset({0x10, 0x11, 0x12}), 0x4B)
        self.events = []

    def _queue(self, event):
        self.events.append(event)

    def _fail_open(self):
        raise AssertionError("fail open")


def _press(hooks, owner, vk):
    data = _KeyData(vk, 0, 0, 0, 0)
    return hooks.physical_key(owner, 0, 0x100, ctypes.addressof(data))


def test_hook_sees_kill_chord_even_while_suppressing(hooks):
    owner = _Owner(suppress=True)
    for vk in (0xA2, 0xA4, 0xA0):  # Left Ctrl, Left Alt, Left Shift.
        _press(hooks, owner, vk)
    assert _press(hooks, owner, 0x4B) == 1  # Swallowed from apps ...
    assert ("kill",) in owner.events  # ... but still engages the kill switch.


def test_hook_ignores_k_without_modifiers(hooks):
    owner = _Owner(suppress=False)
    _press(hooks, owner, 0xA2)
    _press(hooks, owner, 0x4B)
    assert ("kill",) not in owner.events


def test_bring_dialog_forward_waits_for_window_then_forces_focus():
    from windows_mcp.safety.win32 import bring_dialog_forward

    seen = iter([None, None, 42])
    forced = []
    ok = bring_dialog_forward(
        lambda: next(seen), lambda h: forced.append(h) or True, threading.Event(), 1, 0
    )
    assert ok and forced == [42]


def test_bring_dialog_forward_stops_when_dialog_closed():
    from windows_mcp.safety.win32 import bring_dialog_forward

    stop = threading.Event()
    stop.set()
    assert not bring_dialog_forward(lambda: 1, lambda h: True, stop)


def test_force_foreground_attaches_and_retries_with_alt(monkeypatch):
    from windows_mcp.safety import win32

    calls = []

    class U:
        fg = 7

        def GetForegroundWindow(self):
            return self.fg

        def GetWindowThreadProcessId(self, h, p):
            return 70

        def AttachThreadInput(self, a, b, on):
            calls.append(("attach", on))
            return True

        def BringWindowToTop(self, h):
            pass

        def SetActiveWindow(self, h):
            pass

        def SetForegroundWindow(self, h):
            calls.append("setfg")
            if "alt" in calls:  # Refused until this process sent the last input.
                self.fg = h

    monkeypatch.setattr(
        win32.ctypes,
        "WinDLL",
        lambda name, **k: types.SimpleNamespace(GetCurrentThreadId=lambda: 5),
        raising=False,
    )
    assert win32._force_foreground(U(), 42, lambda: calls.append("alt"))
    assert calls.count("setfg") == 2 and "alt" in calls
    assert calls.count(("attach", True)) == calls.count(("attach", False)) == 2


def test_begin_confirmation_stops_key_suppression(monkeypatch):
    win32 = types.ModuleType("windows_mcp.desktop.control_win32")
    win32._MouseHookData = win32._KeyHookData = object
    win32._user32 = None
    win32._key = lambda vk: vk
    win32._interactive_desktop = lambda: True
    win32.run_input_monitor = lambda owner: None
    stub = types.ModuleType("windows_mcp.desktop.control_hooks")
    monkeypatch.setitem(sys.modules, "windows_mcp.desktop.control_win32", win32)
    monkeypatch.setitem(sys.modules, "windows_mcp.desktop.control_hooks", stub)
    monkeypatch.delitem(sys.modules, "windows_mcp.desktop.control", raising=False)
    import windows_mcp.desktop as desktop_pkg

    monkeypatch.setattr(desktop_pkg, "control_hooks", stub, raising=False)
    control = importlib.import_module("windows_mcp.desktop.control")
    try:
        owner = control.ControlCoordinator()
        with owner._lock:
            owner._set_locked("ready")
        token = owner.begin_call("Click")
        owner.end_call(token)  # Lease still held: state "ai".
        assert owner.status()["state"] == "ai"
        owner.begin_confirmation()
        assert owner.status()["state"] == "ready"
        assert owner._suppress is False
    finally:
        sys.modules.pop("windows_mcp.desktop.control", None)


def _esc_sequence(states):
    it = iter(states)
    last = [False]

    def down():
        last[0] = next(it, last[0])
        return last[0]

    return down


def test_escape_answers_no_when_dialog_focused():
    from windows_mcp.safety.win32 import watch_escape

    answered = []
    ok = watch_escape(
        lambda: 42,
        lambda h: True,
        _esc_sequence([False, False, True]),
        answered.append,
        threading.Event(),
        poll_seconds=0,
    )
    assert ok and answered == [42]


def test_escape_ignored_when_dialog_not_foreground():
    from windows_mcp.safety.win32 import watch_escape

    stop = threading.Event()
    answered = []
    threading.Timer(0.1, stop.set).start()
    ok = watch_escape(
        lambda: 42, lambda h: False, lambda: True, answered.append, stop, poll_seconds=0.005
    )
    assert not ok and answered == []


def test_escape_held_before_dialog_is_not_an_answer():
    from windows_mcp.safety.win32 import watch_escape

    stop = threading.Event()
    answered = []
    threading.Timer(0.1, stop.set).start()
    ok = watch_escape(
        lambda: 42, lambda h: True, lambda: True, answered.append, stop, poll_seconds=0.005
    )
    assert not ok and answered == []


def test_escape_watch_stops_when_dialog_closed():
    from windows_mcp.safety.win32 import watch_escape

    stop = threading.Event()
    stop.set()
    assert not watch_escape(lambda: 42, lambda h: True, lambda: False, print, stop)
