"""With takeover = "hotkey" only the takeover chord counts as a person."""

import ctypes

import pytest

from windows_mcp.desktop import control, control_hooks

CHORD = (0x11, 0x12, 0x10, 0x08)  # Ctrl, Alt, Shift, Backspace.


def controller(takeover: str = "hotkey") -> control.ControlCoordinator:
    owner = control.ControlCoordinator()
    owner.takeover = takeover
    with owner._lock:
        owner._set_locked("ready")
    return owner


@pytest.fixture(autouse=True)
def pass_through(monkeypatch):
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)


def key(owner, vk: int, message: int = 0x100) -> int:
    data = control._KeyHookData()
    data.vkCode = vk
    return owner._physical_key(0, message, ctypes.addressof(data))


def mouse(owner, x: int, y: int, message: int = 0x200) -> int:
    data = control._MouseHookData()
    data.pt.x, data.pt.y = x, y
    return owner._physical_mouse(0, message, ctypes.addressof(data))


def drain(owner) -> None:
    while not owner._events.empty():
        owner._handle(owner._events.get_nowait())


def test_default_takeover_is_any():
    owner = control.ControlCoordinator()
    assert owner.takeover == "any"
    assert owner.status()["takeover"] == "any"


def test_status_reports_takeover():
    assert controller("hotkey").status()["takeover"] == "hotkey"


def test_default_mode_key_makes_user_active():
    owner = controller("any")
    assert key(owner, 0x41) == 7
    assert owner._last_physical_event > 0
    drain(owner)
    assert owner.status()["state"] == "user"


def test_default_mode_hook_still_queues_input():
    owner = controller("any")
    key(owner, 0x41)
    mouse(owner, 10, 10)
    assert [event[0] for event in list(owner._events.queue)] == ["key", "point"]


def test_ready_state_ignores_key_and_mouse():
    owner = controller()
    assert key(owner, 0x41) == 7
    assert key(owner, 0x41, 0x101) == 7
    assert mouse(owner, 10, 10) == 7
    assert mouse(owner, 500, 400, 0x201) == 7
    assert mouse(owner, 500, 400, 0x202) == 7
    drain(owner)
    assert owner._last_physical_event == 0.0
    assert owner._events.empty()
    assert owner.status()["state"] == "ready"
    token = owner.begin_call("Click")  # Not USER_CONTROL.
    owner.end_call(token)


def test_ready_state_ignores_handled_input_events():
    owner = controller()
    for event in (("key",), ("point", 5, 5, 0x200), ("raw", 1, 900, 900)):
        owner._handle(event)
    assert owner.status()["state"] == "ready"
    assert owner._last_user == 0.0


def test_ai_state_ignores_noise_and_stays_suppressed():
    owner = controller()
    owner.set_health_probe(lambda: True)
    token = owner.begin_call("Type")
    owner.arm_visible(owner.status()["generation"])
    assert owner._suppress
    assert key(owner, 0x41) == 1  # The cover still swallows physical input.
    assert mouse(owner, 10, 10) == 1
    assert mouse(owner, 700, 700) == 1
    drain(owner)
    owner.checkpoint(token)
    status = owner.status()
    assert status["state"] == "ai"
    assert owner._last_physical_event == 0.0
    assert owner._suppress


def test_ai_state_unarmed_indicator_does_not_mark_user():
    owner = controller()
    owner.set_health_probe(lambda: True)  # Indicator never acknowledged: not armed.
    owner.begin_call("Type")
    assert not owner._visual_armed
    owner._handle(("key",))
    owner._handle(("point", 1, 1, 0x200))
    assert owner.status()["state"] == "ai"


def test_large_raw_motion_does_not_take_over(monkeypatch):
    owner = controller()
    token = owner.begin_call("Move")
    monkeypatch.setattr(control_hooks, "_read_raw_mouse", lambda lparam: (1, 900, 900))
    owner._raw_input(0)
    assert owner._events.empty()
    assert owner._last_physical_event == 0.0
    owner._handle(("raw", 1, 900, 900))
    owner.checkpoint(token)
    assert owner.status()["state"] == "ai"


def test_default_mode_raw_motion_still_takes_over(monkeypatch):
    owner = controller("any")
    owner.begin_call("Move")
    monkeypatch.setattr(control_hooks, "_read_raw_mouse", lambda lparam: (1, 900, 900))
    owner._raw_input(0)
    drain(owner)
    assert owner.status()["state"] == "user"


@pytest.mark.parametrize("press_order", [CHORD, (0x10, 0x12, 0x11, 0x08)])
def test_chord_takes_over(press_order):
    owner = controller()
    owner.begin_call("Type")
    owner._deadline = control.time.monotonic() + 1
    for vk in press_order[:-1]:
        assert key(owner, vk) == 1
    assert not owner._fast_takeover
    assert key(owner, press_order[-1]) == 1
    assert owner._fast_takeover
    assert not owner._suppress
    assert owner._last_physical_event > 0
    for vk in reversed(press_order):
        assert key(owner, vk, 0x101) == 1
    assert not owner._quarantine
    drain(owner)
    status = owner.status()
    assert status["state"] == "user"
    assert status["wait_seconds"] > 0
    with pytest.raises(control.ControlBlocked) as blocked:
        owner.begin_call("Click")
    assert blocked.value.code == "USER_CONTROL"


def test_noise_after_takeover_does_not_extend_idle_wait(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: clock[0])
    owner = controller()
    owner.begin_call("Type")
    owner._deadline = 101.0
    for vk in CHORD:
        key(owner, vk)
    for vk in reversed(CHORD):
        key(owner, vk, 0x101)
    drain(owner)
    assert owner.status()["state"] == "user"
    clock[0] = 105.0
    key(owner, 0x41)
    key(owner, 0x41, 0x101)
    mouse(owner, 300, 300)
    drain(owner)
    clock[0] = 110.0
    assert owner.status()["state"] == "ready"


def test_backspace_alone_is_noise():
    owner = controller()
    owner.begin_call("Type")
    owner._deadline = control.time.monotonic() + 1
    assert key(owner, 0x08) == 1
    assert not owner._fast_takeover
    drain(owner)
    assert owner.status()["state"] == "ai"


def test_held_noise_key_is_still_tracked_for_input_safety():
    owner = controller()
    key(owner, 0x41)
    assert 0x41 in owner._pressed
    with pytest.raises(control.ControlBlocked) as blocked:
        owner.begin_call("Type")
    assert blocked.value.code == "PHYSICAL_INPUT_HELD"
    key(owner, 0x41, 0x101)
    owner.end_call(owner.begin_call("Type"))
