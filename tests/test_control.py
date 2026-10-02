"""State and fail-open tests for physical desktop ownership."""

import ctypes
import queue
import threading
from unittest.mock import Mock

import pytest

from windows_mcp.desktop import control
from windows_mcp.desktop import service
from windows_mcp.desktop.control_context import StepCounter
from windows_mcp.desktop.control_ledger import InputLedger, InputUnavailable


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


def test_type_stops_before_next_character_after_preemption(monkeypatch):
    owner = Mock()
    checks = [None, None, control.ControlBlocked("CONTROL_PREEMPTED", {"state": "user"})]
    owner.checkpoint_current.side_effect = checks
    monkeypatch.setattr(service, "get_controller", lambda: owner)
    click = Mock()
    send = Mock()
    monkeypatch.setattr(service.uia, "Click", click)
    monkeypatch.setattr(service.uia, "SendKeys", send)
    desktop = service.Desktop.__new__(service.Desktop)
    with pytest.raises(control.ControlBlocked):
        desktop.type((1, 2), "abc")
    assert send.call_count == 1
    send.assert_any_call("a", interval=0.04, waitTime=0.05)


def test_drag_releases_its_mouse_button_after_preemption(monkeypatch):
    owner = Mock()
    owner.input_ledger = InputLedger()
    owner.checkpoint_current.side_effect = [
        None,
        None,
        control.ControlBlocked("CONTROL_PREEMPTED", {"state": "user"}),
    ]
    owner.physical_mouse_down.return_value = False
    monkeypatch.setattr(service, "get_controller", lambda: owner)
    monkeypatch.setattr(service, "sleep", lambda _: None)
    monkeypatch.setattr(service.uia, "GetCursorPos", lambda: (0, 0))
    press = Mock()
    release = Mock()
    monkeypatch.setattr(service.uia, "PressMouse", press)
    monkeypatch.setattr(service.uia, "ReleaseMouse", release)
    desktop = service.Desktop.__new__(service.Desktop)
    with pytest.raises(control.ControlBlocked):
        desktop.drag((100, 0))
    press.assert_called_once()
    release.assert_called_once()


def test_ai_hold_is_released_if_press_raises_after_injection():
    ledger = InputLedger()
    sent = []

    def down_then_raise():
        sent.append("down")
        raise RuntimeError("wait failed after injection")

    with pytest.raises(RuntimeError, match="wait failed"):
        ledger.press("key:shift", down_then_raise, lambda: sent.append("up"), lambda: False)
    assert sent == ["down", "up"]
    assert ledger.pending() == ()


def test_ai_hold_defers_up_only_when_physical_down_reached_app():
    ledger = InputLedger()
    sent = []
    ledger.press("mouse:left", lambda: sent.append("down"), lambda: sent.append("up"), lambda: True)
    ledger.release("mouse:left")
    assert sent == ["down"]
    assert ledger.pending() == ()


def test_fail_open_blocks_new_press_and_clears_before_notification():
    owner = ready_controller()
    owner.begin_call("Drag")
    actions = []
    owner.input_ledger.press(
        "mouse:left", lambda: actions.append("down"), lambda: actions.append("up"), lambda: False
    )
    owner.subscribe(lambda _: actions.append("notified"))
    owner._fail_open()
    with pytest.raises(InputUnavailable, match="unavailable"):
        owner.input_ledger.press(
            "key:shift", lambda: actions.append("late down"), lambda: None, lambda: False
        )
    owner._mark_unavailable()
    assert actions == ["down", "up", "notified"]


def test_fail_open_wakes_existing_watchdog_to_release_ai_hold(monkeypatch):
    owner = ready_controller()
    owner.begin_call("Drag")
    released = threading.Event()
    owner.input_ledger.press("mouse:left", lambda: None, released.set, lambda: False)
    owner._thread = Mock()
    owner._thread.is_alive.return_value = True
    owner._thread_beat = control.time.monotonic()
    monkeypatch.setattr(control, "_interactive_desktop", lambda: True)
    watcher = threading.Thread(target=owner._watch)
    watcher.start()
    try:
        owner._fail_open()
        assert owner._release_requested.is_set() or released.is_set()
        assert released.wait(1.0)
        assert owner.input_ledger.pending() == ()
    finally:
        owner._stop.set()
        owner._release_requested.set()
        watcher.join(2)
    assert not watcher.is_alive()


def test_fail_open_releases_before_slow_health_probe(monkeypatch):
    owner = ready_controller()
    owner.begin_call("Type")
    released, probe_entered, probe_continue = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    owner.input_ledger.press("key:ctrl", lambda: None, released.set, lambda: False)
    owner._thread = Mock()
    owner._thread.is_alive.return_value = True
    owner._thread_beat = control.time.monotonic()
    monkeypatch.setattr(control, "_interactive_desktop", lambda: True)

    def slow_probe():
        probe_entered.set()
        probe_continue.wait(2)
        return True

    owner.set_health_probe(slow_probe)
    # The hook path signals cleanup; it never waits for a UI health probe.
    owner._fail_open()
    watcher = threading.Thread(target=owner._watch)
    watcher.start()
    try:
        assert released.wait(1)
        assert not probe_entered.is_set()
    finally:
        probe_continue.set()
        owner._stop.set()
        owner._release_requested.set()
        watcher.join(2)
    assert not watcher.is_alive()


def test_takeover_after_checkpoint_blocks_new_ai_down(monkeypatch):
    owner = ready_controller()
    token = owner.begin_call("Drag")
    owner.checkpoint(token)
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 7)
    data = control._MouseHookData()
    assert owner._physical_mouse(0, 0x200, ctypes.addressof(data)) == 1
    assert owner._fast_pending
    with pytest.raises(InputUnavailable, match="unavailable"):
        owner.input_ledger.press(
            "mouse:left", lambda: pytest.fail("late AI down"), lambda: None, lambda: False
        )
    owner._handle(owner._events.get_nowait())
    assert owner.status()["state"] == "takeover_pending"
    with pytest.raises(control.ControlBlocked):
        owner.checkpoint(token)


@pytest.mark.parametrize("action", ["scroll", "drag", "multi_select"])
def test_takeover_between_checkpoint_and_press_injects_no_down(monkeypatch, action):
    owner = Mock()
    owner.input_ledger = InputLedger()
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == (2 if action == "drag" else 1):
            owner.input_ledger.block_new()

    owner.checkpoint_current.side_effect = checkpoint
    monkeypatch.setattr(service, "get_controller", lambda: owner)
    monkeypatch.setattr(service, "sleep", lambda _: None)
    monkeypatch.setattr(service.uia, "GetCursorPos", lambda: (0, 0))
    key_down, mouse_down = Mock(), Mock()
    monkeypatch.setattr(service.uia, "PressKey", key_down)
    monkeypatch.setattr(service.uia, "PressMouse", mouse_down)
    desktop = service.Desktop.__new__(service.Desktop)
    with pytest.raises(InputUnavailable):
        if action == "scroll":
            desktop.scroll(type="horizontal", direction="left")
        elif action == "drag":
            desktop.drag((100, 0))
        else:
            desktop.multi_select(press_ctrl=True, locs=[(100, 100)])
    key_down.assert_not_called()
    mouse_down.assert_not_called()
    assert owner.input_ledger.pending() == ()


def test_new_lease_reenables_ledger_after_cancelled_mouse_candidate():
    owner = ready_controller()
    token = owner.begin_call("Move")
    owner.input_ledger.block_new()  # Physical move's immediate guard.
    owner._handle(("point", 0, 0, 0x200))
    assert owner.status()["state"] == "takeover_pending"
    owner._last_move -= 0.4
    assert owner.status()["state"] == "ai"
    with pytest.raises(control.ControlBlocked):
        owner.checkpoint(token)
    owner.begin_call("Click")
    actions = []
    owner.input_ledger.press(
        "mouse:left", lambda: actions.append("down"), lambda: actions.append("up"), lambda: False
    )
    owner.input_ledger.release("mouse:left")
    assert actions == ["down", "up"]


def test_release_waits_for_inflight_press_then_clears_hold():
    ledger = InputLedger()
    entered, finish = threading.Event(), threading.Event()
    actions = []

    def down():
        entered.set()
        assert finish.wait(2)
        actions.append("down")

    pressing = threading.Thread(
        target=lambda: ledger.press("key:ctrl", down, lambda: actions.append("up"), lambda: False)
    )
    pressing.start()
    assert entered.wait(2)
    ledger.block_new()
    releasing = threading.Thread(target=ledger.release_all)
    releasing.start()
    finish.set()
    pressing.join(2)
    releasing.join(2)
    assert not pressing.is_alive() and not releasing.is_alive()
    assert actions == ["down", "up"]
    assert ledger.pending() == ()


def test_release_all_attempts_other_holds_after_one_failure():
    ledger = InputLedger()
    events = []

    def fail_up():
        raise RuntimeError("up failed")

    ledger.press("key:ctrl", lambda: None, fail_up, lambda: False)
    ledger.press("mouse:left", lambda: None, lambda: events.append("mouse up"), lambda: False)
    with pytest.raises(RuntimeError, match="up failed"):
        ledger.release_all()
    assert events == ["mouse up"]
    assert ledger.pending() == ("key:ctrl",)


def test_recovery_requires_ledger_clear_health_and_interactive_desktop(monkeypatch):
    owner = ready_controller()
    owner.begin_call("Drag")
    attempts = 0

    def up():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporarily blocked")

    owner.input_ledger.press("mouse:left", lambda: None, up, lambda: False)
    owner._fail_open()
    owner._recover_after_rehook()
    assert owner.status()["state"] == "unavailable"
    owner.set_health_probe(lambda: False)
    owner._recover_after_rehook()
    assert owner.status()["state"] == "unavailable"
    owner.set_health_probe(lambda: True)
    monkeypatch.setattr(control, "_interactive_desktop", lambda: False)
    owner._recover_after_rehook()
    assert owner.status()["state"] == "unavailable"
    monkeypatch.setattr(control, "_interactive_desktop", lambda: True)
    owner._recover_after_rehook()
    assert owner.status()["state"] == "user"
    assert owner.input_ledger.pending() == ()


def test_preemption_reports_executed_steps():
    owner = ready_controller()
    token = owner.begin_call("Type")
    context = control.current_token.set(token)
    step_context = control.current_steps.set(StepCounter())
    try:
        owner.record_step_current()
        owner.record_step_current()
        owner._handle(("hotkey",))
        with pytest.raises(control.ControlBlocked) as exc:
            owner.checkpoint_current()
        assert exc.value.status["executed_steps"] == 2
    finally:
        control.current_steps.reset(step_context)
        control.current_token.reset(context)
