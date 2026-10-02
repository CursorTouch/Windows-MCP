"""AI input checkpoints, ledger cleanup, and recovery tests."""

import ctypes
import threading
from unittest.mock import Mock

import pytest

from windows_mcp.desktop import control, service
from windows_mcp.desktop.control_context import StepCounter
from windows_mcp.desktop.control_ledger import InputLedger, InputUnavailable


def ready_controller() -> control.ControlCoordinator:
    owner = control.ControlCoordinator()
    with owner._lock:
        owner._set_locked("ready")
    return owner


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
