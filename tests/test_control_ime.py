"""Replay Japanese NLS mode events without installing hooks or sending input."""

import ctypes
from unittest.mock import Mock

import pytest

from windows_mcp.desktop import control
from windows_mcp.desktop.control_recovery import StaleInputRecovery


@pytest.fixture
def case(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: clock[0])
    next_hook = Mock(return_value=0)
    monkeypatch.setattr(control._user32, "CallNextHookEx", next_hook)
    owner = control.ControlCoordinator()
    owner._state = "ready"
    owner.idle_timeout_seconds = 3
    return owner, clock, next_hook


def send_key(owner, vk, message, scan=0x29, flags=0):
    data = control._KeyHookData()
    data.vkCode, data.scanCode, data.flags = vk, scan, flags
    return owner._physical_key(0, message, ctypes.addressof(data))


def drain(owner):
    while not owner._events.empty():
        owner._handle(owner._events.get_nowait())


@pytest.mark.parametrize("old_vk,new_vk", [(0xF4, 0xF3), (0xF3, 0xF4)])
def test_mode_toggle_returns_to_ready_after_idle(case, old_vk, new_vk):
    owner, clock, _ = case
    send_key(owner, old_vk, 0x101, flags=0x80)
    send_key(owner, new_vk, 0x100)
    drain(owner)
    assert owner.status()["state"] == "user"
    assert owner._last_physical_event == clock[0]
    with pytest.raises(control.ControlBlocked):
        owner.begin_call("Move")
    clock[0] = 102.999
    assert owner.status()["state"] == "user"

    # The active IME mode still has its async high bit set after physical release.
    recovery = StaleInputRecovery()
    for i in range(7):
        clock[0] = 103.0 + i * 0.11
        recovery.sample(owner, clock[0], lambda vk: -32768 if vk == new_vk else 0)
    assert owner.status()["state"] == "ready"
    assert not owner._pressed
    assert not owner._delivered_keys
    token = owner.begin_call("Move")
    owner.checkpoint(token)
    owner.end_call(token)


@pytest.mark.parametrize("vk", [0xF3, 0xF4])
@pytest.mark.parametrize("blocked", [0, 7])
def test_mode_event_is_forwarded_unchanged_without_a_delivered_hold(case, vk, blocked):
    owner, clock, next_hook = case
    next_hook.return_value = blocked
    data = control._KeyHookData()
    data.vkCode, data.scanCode = vk, 0x29
    address = ctypes.addressof(data)
    assert owner._physical_key(0, 0x100, address) == blocked
    next_hook.assert_called_once_with(owner._key_hook, 0, 0x100, address)
    assert (data.vkCode, data.scanCode, data.flags) == (vk, 0x29, 0)
    assert not owner.physical_key_down(vk)
    assert owner._last_physical_event == clock[0]
    assert owner._events.get_nowait() == ("key",)


@pytest.mark.parametrize("vk", [0xF3, 0xF4])
def test_mode_event_is_still_suppressed_during_ai_control(case, vk):
    owner, _, next_hook = case
    token = owner.begin_call("Move")
    assert send_key(owner, vk, 0x100) == 1
    next_hook.assert_not_called()
    drain(owner)
    owner.checkpoint(token)
    assert owner._suppress
    assert not owner._pressed
    assert not owner._delivered_keys
    owner.end_call(token)


def test_mode_event_at_expired_deadline_still_fails_open(case):
    owner, clock, next_hook = case
    owner.begin_call("Move")
    clock[0] += 2
    assert send_key(owner, 0xF3, 0x100) == 0
    next_hook.assert_called_once()
    assert owner._emergency and not owner._suppress
    assert owner.status()["state"] == "unavailable"


@pytest.mark.parametrize("vk,scan,flags", [(0xF3, 0x28, 0), (0xF4, 0x29, 1), (0x41, 0x29, 0)])
def test_other_native_key_events_remain_physical_holds(case, vk, scan, flags):
    owner, clock, _ = case
    send_key(owner, vk, 0x100, scan, flags)
    drain(owner)
    clock[0] += 20
    assert owner._pressed == {vk}
    assert owner.physical_key_down(vk)
    assert owner.status()["state"] == "user"
    with pytest.raises(control.ControlBlocked, match="PHYSICAL_INPUT_HELD"):
        owner.begin_call("Move")
    send_key(owner, vk, 0x101, scan, flags | 0x80)
    drain(owner)
    clock[0] += 3
    assert owner.status()["state"] == "ready"
    assert not owner.physical_key_down(vk)


@pytest.mark.parametrize("kind", ["key", "mouse"])
def test_mode_toggle_does_not_release_another_held_input(case, kind):
    owner, clock, _ = case
    if kind == "key":
        send_key(owner, 0x41, 0x100, scan=0x1E)
    else:
        data = control._MouseHookData()
        owner._physical_mouse(0, 0x201, ctypes.addressof(data))
    send_key(owner, 0xF4, 0x101, flags=0x80)
    send_key(owner, 0xF3, 0x100)
    drain(owner)
    clock[0] += 20
    assert owner.status()["state"] == "user"
    with pytest.raises(control.ControlBlocked, match="PHYSICAL_INPUT_HELD"):
        owner.begin_call("Move")
    assert owner.physical_key_down(0x41) if kind == "key" else owner.physical_mouse_down("left")


@pytest.mark.parametrize("vk", [0xF3, 0xF4])
def test_injected_mode_event_does_not_count_as_user_activity(case, vk):
    owner, _, next_hook = case
    token = owner.begin_call("Move")
    assert send_key(owner, vk, 0x100, flags=0x10) == 0
    next_hook.assert_called_once()
    assert owner._last_physical_event == 0
    assert owner._events.empty()
    assert not owner._pressed and not owner._delivered_keys
    owner.checkpoint(token)
    owner.end_call(token)
