"""State and fail-open tests for physical desktop ownership."""

import ctypes
import queue
from unittest.mock import Mock

import pytest

from windows_mcp.desktop import control


def ready_controller() -> control.ControlCoordinator:
    owner = control.ControlCoordinator()
    with owner._lock:
        owner._set_locked("ready")
    return owner


def test_start_is_ready_without_physical_input(monkeypatch):
    owner = control.ControlCoordinator()
    monkeypatch.setattr(control, "_interactive_desktop", lambda: True)

    def run():
        owner._thread_beat = control.time.monotonic()
        owner._ready.set()
        owner._stop.wait()

    monkeypatch.setattr(owner, "_run", run)
    owner.start()
    try:
        assert owner.status()["state"] == "ready"
        token = owner.begin_call("Wait")
        owner.checkpoint(token)
        owner.end_call(token)
    finally:
        owner.stop()


def test_indicator_ack_precedes_suppression_and_physical_input_wins(monkeypatch):
    owner = ready_controller()
    owner.set_health_probe(lambda: True)
    observed = []
    owner.subscribe(lambda status: observed.append((status["state"], owner._suppress)))
    token = owner.begin_call("Type")
    assert observed == [("ai", False)]
    with pytest.raises(control.ControlBlocked):
        owner.checkpoint(token)

    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)
    event = control._KeyHookData()
    event.vkCode = 0x41
    assert owner._physical_key(0, 0x100, ctypes.addressof(event)) == 7
    owner.arm_visible(token)  # A late visual acknowledgement cannot reclaim the key.
    assert not owner._suppress
    owner._handle(owner._events.get_nowait())
    assert owner.status()["state"] == "user"


def test_indicator_ack_arms_and_capture_pause_releases_physical_input():
    owner = ready_controller()
    owner.set_health_probe(lambda: True)
    owner.subscribe(
        lambda status: owner.arm_visible(status["generation"]) if status["state"] == "ai" else None
    )
    token = owner.begin_call("Screenshot")
    owner.checkpoint(token)
    assert owner._suppress

    generation = owner.pause_for_capture()
    assert generation == token
    assert not owner._suppress
    with pytest.raises(control.ControlBlocked):
        owner.checkpoint(token)
    owner.resume_after_capture(generation, restored=True)
    owner.checkpoint(token)
    assert owner._suppress


def test_capture_restore_failure_never_rearms():
    owner = ready_controller()
    owner.set_health_probe(lambda: True)
    owner.subscribe(
        lambda status: owner.arm_visible(status["generation"]) if status["state"] == "ai" else None
    )
    owner.begin_call("Screenshot")
    generation = owner.pause_for_capture()
    owner.resume_after_capture(generation, restored=False)
    assert owner._emergency and not owner._suppress


def test_stale_capture_generation_never_rearms_after_takeover():
    owner = ready_controller()
    owner.set_health_probe(lambda: True)
    owner.subscribe(
        lambda status: owner.arm_visible(status["generation"]) if status["state"] == "ai" else None
    )
    owner.begin_call("Screenshot")
    generation = owner.pause_for_capture()
    owner._handle(("key",))
    assert owner.status()["state"] == "user"
    owner.resume_after_capture(generation, restored=True)
    assert not owner._suppress


def test_watchdog_does_not_rearm_hidden_indicator(monkeypatch):
    owner = ready_controller()
    owner.set_health_probe(lambda: True)
    owner.subscribe(
        lambda status: owner.arm_visible(status["generation"]) if status["state"] == "ai" else None
    )
    owner.begin_call("Screenshot")
    owner.pause_for_capture()
    owner._thread = Mock()
    owner._thread.is_alive.return_value = True
    owner._thread_beat = control.time.monotonic()
    owner._stop.is_set = Mock(side_effect=[False, True])
    owner._release_requested.wait = Mock()
    monkeypatch.setattr(control, "_interactive_desktop", lambda: True)
    owner._watch()
    assert not owner._suppress


@pytest.mark.parametrize("left,right", [(0xA0, 0xA1), (0xA2, 0xA3), (0xA4, 0xA5)])
def test_opposite_modifier_release_does_not_clear_held_key(monkeypatch, left, right):
    clock = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)
    owner = ready_controller()
    owner._handle(("key",))

    def send(vk, message):
        data = control._KeyHookData()
        data.vkCode = vk
        return owner._physical_key(0, message, ctypes.addressof(data))

    send(left, 0x100)
    send(right, 0x100)
    send(left, 0x101)  # Releasing one side must not clear the other.
    assert owner._pressed == {right}
    clock[0] = 120.0
    assert owner.status()["state"] == "user"
    send(right, 0x101)
    clock[0] = 130.0
    assert owner.status()["state"] == "ready"


@pytest.mark.parametrize("generic,side", [(0x10, 0xA0), (0x11, 0xA3), (0x12, 0xA4)])
def test_delivered_modifier_lookup_never_iterates_live_hook_set(generic, side):
    class NoIteration(set):
        def __iter__(self):
            raise AssertionError("hook-owned set must not be iterated")

    owner = ready_controller()
    owner._delivered_keys = NoIteration({side})
    assert owner.physical_key_down(generic)
    assert owner.physical_key_down(side)
    assert not owner.physical_key_down(0x41)


def test_start_preserves_cooldown_after_real_physical_input(monkeypatch):
    owner = control.ControlCoordinator()
    monkeypatch.setattr(control, "_interactive_desktop", lambda: True)

    def run():
        owner._thread_beat = control.time.monotonic()
        owner._last_physical_event = control.time.monotonic()
        owner._ready.set()
        owner._stop.wait()

    monkeypatch.setattr(owner, "_run", run)
    owner.start()
    try:
        assert owner.status()["state"] == "user"
        with pytest.raises(control.ControlBlocked, match="USER_CONTROL"):
            owner.begin_call("Wait")
    finally:
        owner.stop()


def test_physical_mouse_pixel_threshold_preempts_only_at_boundary():
    owner = ready_controller()
    owner.begin_call("Click")
    owner._handle(("point", 100, 100, 0x200))
    owner._handle(("point", 219, 100, 0x200))
    assert owner.status()["state"] == "takeover_pending"
    owner._handle(("point", 220, 100, 0x200))
    assert owner.status()["state"] == "user"
    with pytest.raises(control.ControlBlocked) as exc:
        owner.begin_call("Click")
    assert exc.value.code == "USER_CONTROL"


def test_raw_mouse_is_per_device_and_relative():
    owner = ready_controller()
    owner.begin_call("Move")
    owner._handle(("raw", 1, 119, 0))
    owner._handle(("raw", 2, 119, 0))
    assert owner.status()["state"] == "takeover_pending"
    owner._handle(("raw", 2, 1, 0))
    assert owner.status()["state"] == "user"


def test_raw_device_switch_resets_previous_candidate():
    owner = ready_controller()
    owner.begin_call("Move")
    owner._handle(("raw", 1, 100, 0))
    owner._handle(("raw", 2, 100, 0))
    owner._handle(("raw", 1, 100, 0))
    assert owner.status()["state"] == "takeover_pending"
    owner._handle(("raw", 1, 20, 0))
    assert owner.status()["state"] == "user"


def test_user_idle_resumes_at_ten_seconds(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: clock[0])
    owner = ready_controller()
    owner._handle(("key",))
    assert owner.status()["state"] == "user"
    clock[0] = 109.999
    assert owner.status()["state"] == "user"
    owner._handle(("key",))
    clock[0] = 119.998
    assert owner.status()["state"] == "user"
    clock[0] = 119.999
    assert owner.status()["state"] == "ready"


def test_user_idle_waits_until_held_mouse_is_released(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: clock[0])
    owner = ready_controller()
    owner._handle(("key",))
    owner._mouse_down.add(1)
    clock[0] = 111.0
    assert owner.status()["state"] == "user"
    owner._mouse_down.clear()
    owner._handle(("point", 10, 10, 0x202))
    clock[0] = 120.999
    assert owner.status()["state"] == "user"
    clock[0] = 121.0
    assert owner.status()["state"] == "ready"


def test_user_idle_counts_physical_up_before_queue_consumption(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)
    owner = ready_controller()
    owner._handle(("key",))
    owner._pressed.add(0x41)
    clock[0] = 111.0
    event = control._KeyHookData()
    event.vkCode = 0x41
    assert owner._physical_key(0, 0x101, ctypes.addressof(event)) == 7
    # The event remains queued, but the hook timestamp already blocks resume.
    assert owner.status()["state"] == "user"
    clock[0] = 120.999
    assert owner.status()["state"] == "user"
    clock[0] = 121.0
    assert owner.status()["state"] == "ready"


def test_pending_does_not_expire_while_mouse_button_is_held(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: clock[0])
    owner = ready_controller()
    owner.begin_call("Click")
    owner._handle(("point", 10, 10, 0x200))
    owner._mouse_down.add(1)
    clock[0] = 101.0
    assert owner.status()["state"] == "takeover_pending"
    owner._mouse_down.clear()
    assert owner.status()["state"] == "ai"


def test_pending_cancels_without_replaying_stale_call(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: clock[0])
    owner = ready_controller()
    token = owner.begin_call("Type")
    owner._handle(("point", 10, 10, 0x200))
    with pytest.raises(control.ControlBlocked):
        owner.checkpoint(token)
    owner.end_call(token)
    clock[0] = 100.301
    assert owner.status()["state"] == "ai"
    with pytest.raises(control.ControlBlocked):
        owner.checkpoint(token)


@pytest.mark.parametrize("press_order", [(0x11, 0x12, 0x10, 0x08), (0x10, 0x12, 0x11, 0x08)])
def test_hotkey_fast_path_releases_before_coordinator_and_quarantines_up(monkeypatch, press_order):
    owner = ready_controller()
    owner.begin_call("Type")
    owner._deadline = control.time.monotonic() + 1
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)

    def send(vk, message):
        data = control._KeyHookData()
        data.vkCode = vk
        return owner._physical_key(0, message, ctypes.addressof(data))

    for vk in press_order:
        assert send(vk, 0x100) == 1
    assert not owner._suppress
    assert owner._fast_takeover
    assert send(0x08, 0x100) == 1  # Repeated Backspace is still swallowed.
    for vk in (0x08, 0x10, 0x12, 0x11):
        assert send(vk, 0x101) == 1
    assert not owner._quarantine
    owner._handle(("hotkey",))
    assert owner.status()["state"] == "user"
    assert send(0x41, 0x100) == 7


def test_backspace_repeat_after_modifiers_does_not_trigger_hotkey(monkeypatch):
    owner = ready_controller()
    owner.begin_call("Type")
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)

    def down(vk):
        data = control._KeyHookData()
        data.vkCode = vk
        return owner._physical_key(0, 0x100, ctypes.addressof(data))

    for vk in (0x08, 0x11, 0x12, 0x10, 0x08):
        assert down(vk) == 1
    assert not owner._fast_takeover
    assert owner._suppress


def test_injected_key_passes_and_queue_full_fails_open(monkeypatch):
    owner = ready_controller()
    owner.begin_call("Type")
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)
    injected = control._KeyHookData()
    injected.vkCode = 0x41
    injected.flags = 0x10
    assert owner._physical_key(0, 0x100, ctypes.addressof(injected)) == 7
    owner._events = queue.Queue(maxsize=1)
    owner._queue(("first",))
    owner._queue(("overflow",))
    assert owner._emergency and not owner._suppress
    assert owner._physical_key(-1, 0x100, ctypes.addressof(injected)) == 7


def test_physical_move_pauses_ai_before_event_queue_is_processed(monkeypatch):
    owner = ready_controller()
    token = owner.begin_call("Move")
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)
    data = control._MouseHookData()
    data.pt.x, data.pt.y = 10, 20
    assert owner._physical_mouse(0, 0x200, ctypes.addressof(data)) == 1
    with pytest.raises(control.ControlBlocked):
        owner.checkpoint(token)
    owner._handle(owner._events.get_nowait())
    assert owner.status()["state"] == "takeover_pending"


def test_swallowed_physical_down_does_not_skip_ai_release(monkeypatch):
    owner = ready_controller()
    owner.begin_call("Drag")
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)
    data = control._MouseHookData()
    assert owner._physical_mouse(0, 0x201, ctypes.addressof(data)) == 1
    assert not owner.physical_mouse_down("left")
    owner._handle(("hotkey",))
    assert owner._physical_mouse(0, 0x201, ctypes.addressof(data)) == 7
    assert owner.physical_mouse_down("left")
    assert owner._physical_mouse(0, 0x202, ctypes.addressof(data)) == 7
    assert not owner.physical_mouse_down("left")


def test_fail_open_updates_status_and_notifies_without_input_thread():
    owner = ready_controller()
    owner.begin_call("Type")
    events = []
    owner.subscribe(events.append)
    owner._fail_open()
    assert owner.status()["state"] == "unavailable"
    assert events[-1]["state"] == "unavailable"


def test_stop_rejects_still_running_hook_thread():
    owner = ready_controller()
    owner._thread = Mock()
    owner._thread.is_alive.return_value = True
    owner._watchdog = None
    with pytest.raises(RuntimeError, match="did not stop cleanly"):
        owner.stop()
    with pytest.raises(RuntimeError, match="has not stopped"):
        owner.start()


def test_watchdog_cannot_refresh_if_coordinator_lock_is_held(monkeypatch):
    owner = ready_controller()
    owner.begin_call("Type")
    owner._thread = Mock()
    owner._thread.is_alive.return_value = True
    owner._thread_beat = control.time.monotonic()
    owner._lock.acquire()
    try:
        owner._stop.is_set = Mock(side_effect=[False, True])
        owner._release_requested.wait = Mock()
        owner._watch()
    finally:
        owner._lock.release()
    assert owner._emergency and not owner._suppress
