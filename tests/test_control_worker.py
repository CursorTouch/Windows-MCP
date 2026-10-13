"""Coordinator-worker scheduling and fault recovery without desktop input."""

import ctypes
import threading
from types import SimpleNamespace

import pytest

from windows_mcp.desktop import control, control_worker


class ClockWake:
    """Advance only at worker waits; never patch the process-wide time module."""

    def __init__(self, owner, clock, limit=60):
        self.owner, self.clock, self.limit = owner, clock, limit
        self.waits = []
        self.signals = 0

    def set(self):
        self.signals += 1

    def clear(self):
        pass

    def wait(self, timeout=None):
        assert timeout is not None and 0 < timeout <= 0.01
        self.waits.append(timeout)
        self.clock[0] += timeout
        self.owner._thread_beat = self.clock[0]
        if len(self.waits) >= self.limit:
            self.owner._stop.set()
        return False


def worker_fixture(monkeypatch, limit=60):
    clock = [100.0]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(control_worker, "time", fake_time)
    monkeypatch.setattr(control, "time", fake_time)
    owner = control.ControlCoordinator()
    owner._thread = SimpleNamespace(is_alive=lambda: True)
    owner._thread_beat = clock[0]
    owner._state = "ready"
    wake = ClockWake(owner, clock, limit)
    owner._release_requested = wake
    return owner, clock, wake


def test_queue_wakes_worker_immediately():
    owner = control.ControlCoordinator()
    owner._release_requested.clear()
    owner._queue(("hotkey",))
    assert owner._release_requested.is_set()
    assert owner._events.get_nowait() == ("hotkey",)


def test_event_flood_cannot_starve_tick_or_stop(monkeypatch):
    owner, _, wake = worker_fixture(monkeypatch, limit=3)
    seen = []

    def handle(event):
        seen.append(event[0])
        if event[0] != "tick":
            owner._queue(("key",))

    monkeypatch.setattr(owner, "_handle", handle)
    owner._queue(("key",))
    control_worker.run_control_worker(owner, lambda: True)
    ticks = [i for i, event in enumerate(seen) if event == "tick"]
    assert ticks and ticks[0] <= 128
    assert all(b - a <= 129 for a, b in zip(ticks, ticks[1:]))
    assert len(wake.waits) == 3


def test_stop_during_batch_prevents_remaining_events(monkeypatch):
    owner, _, _ = worker_fixture(monkeypatch)
    seen = []

    def handle(event):
        seen.append(event)
        owner._stop.set()

    monkeypatch.setattr(owner, "_handle", handle)
    for _ in range(200):
        owner._queue(("key",))
    control_worker.run_control_worker(owner, lambda: True)
    assert seen == [("key",)]


def test_slow_health_checks_are_spaced_from_completion(monkeypatch):
    owner, clock, _ = worker_fixture(monkeypatch, limit=80)
    probes = []

    def health():
        probes.append(clock[0])
        clock[0] += 0.07
        owner._thread_beat = clock[0]
        return True

    owner._health_probe = health
    control_worker.run_control_worker(owner, lambda: True)
    assert len(probes) >= 2
    assert all(b - a >= 0.32 - 1e-8 for a, b in zip(probes, probes[1:]))


def test_fault_cleanup_precedes_any_health_probe(monkeypatch):
    owner, _, _ = worker_fixture(monkeypatch, limit=2)
    owner._fail_open()
    order = []
    monkeypatch.setattr(owner.input_ledger, "release_all", lambda: order.append("release"))
    owner._health_probe = lambda: order.append("health") or True
    control_worker.run_control_worker(owner, lambda: order.append("desktop") or True)
    assert order and order[0] == "release"
    assert "health" not in order and "desktop" not in order


def test_rehook_recovery_uses_only_fresh_throttled_health(monkeypatch):
    owner, clock, _ = worker_fixture(monkeypatch, limit=65)
    samples, recovered = [], []

    def health():
        samples.append(clock[0])
        return True

    def recover(fault=None, *, healthy=None, sampled_at=None):
        assert sampled_at == clock[0]
        recovered.append((clock[0], healthy, fault))
        # Repeated faults/rehooks must not trigger a new expensive probe per tick.
        owner._fail_open()
        owner._rehook_completed = owner._fault_token

    owner._health_probe = health
    monkeypatch.setattr(owner, "_recover_after_rehook", recover)
    owner._fail_open()
    owner._rehook_completed = owner._fault_token
    control_worker.run_control_worker(owner, lambda: True)
    assert recovered
    assert all(healthy is True and stamp in samples for stamp, healthy, _ in recovered)
    assert len(recovered) <= len(samples)
    assert all(b - a >= 0.25 - 1e-8 for a, b in zip(samples, samples[1:]))


def test_failed_health_does_not_recover_rehook(monkeypatch):
    owner, _, _ = worker_fixture(monkeypatch, limit=2)
    owner._fail_open()
    owner._rehook_completed = owner._fault_token
    owner._health_probe = lambda: False
    recovered = []
    monkeypatch.setattr(owner, "_recover_after_rehook", lambda *a, **k: recovered.append(k))
    control_worker.run_control_worker(owner, lambda: True)
    assert not any(item.get("healthy") is True for item in recovered)
    assert owner._emergency and owner.input_ledger._blocked


@pytest.mark.parametrize("interruption", ["stale", "enable_fault", "state_fault", "stop"])
def test_recovery_cannot_clear_new_fault_or_stop(monkeypatch, interruption):
    owner = control.ControlCoordinator()
    owner._fail_open()
    fault = owner._fault_token
    owner._rehook_completed = fault
    if interruption == "stale":
        owner._fail_open()
    elif interruption == "stop":
        owner._stop.set()
    elif interruption == "enable_fault":
        enable = owner.input_ledger.enable

        def interrupt_enable():
            enable()
            owner._fail_open()

        monkeypatch.setattr(owner.input_ledger, "enable", interrupt_enable)
    else:
        change_state = owner._set_locked

        def interrupt_state(state):
            changed = change_state(state)
            if state == "user":
                owner._fail_open()
            return changed

        monkeypatch.setattr(owner, "_set_locked", interrupt_state)
    owner._recover_after_rehook(fault, healthy=True, sampled_at=control.time.monotonic())
    assert owner.input_ledger._blocked
    assert not owner._suppress
    assert owner._emergency or owner._stop.is_set()
    assert owner._state == "unavailable"


def test_successful_recovery_requires_current_completed_installation():
    owner = control.ControlCoordinator()
    owner._fail_open()
    fault = owner._fault_token
    owner._rehook_completed = fault
    owner._recover_after_rehook(fault, healthy=True, sampled_at=control.time.monotonic())
    assert not owner._emergency
    assert owner._state == "user"
    assert not owner._suppress


def test_fault_during_health_probe_invalidates_completed_rehook(monkeypatch):
    owner, _, _ = worker_fixture(monkeypatch, limit=2)
    owner._fail_open()
    old_fault = owner._fault_token
    owner._rehook_completed = old_fault

    def health():
        owner._fail_open()
        return True

    owner._health_probe = health
    control_worker.run_control_worker(owner, lambda: True)
    assert owner._fault_token is not old_fault
    assert owner._emergency and owner.input_ledger._blocked
    assert owner._state == "unavailable"


@pytest.mark.parametrize("interruption", ["stop", "locked"])
def test_deadline_refresh_cannot_rearm_stopped_or_locked_owner(interruption):
    owner = control.ControlCoordinator()
    owner._state = "ai"
    owner._visual_armed = True
    if interruption == "stop":
        owner._stop.set()
        control_worker.refresh_deadline(owner, control.time.monotonic())
    else:
        with owner._lock:
            control_worker.refresh_deadline(owner, control.time.monotonic())
        assert owner._emergency and owner.input_ledger._blocked
    assert not owner._suppress
    assert owner._deadline == 0.0


def test_old_healthy_sample_cannot_renew_a_new_fault_generation():
    owner = control.ControlCoordinator()
    healthy_fault = owner._fault_token
    owner._fail_open()
    owner._state = "ai"
    owner._visual_armed = True
    control_worker.refresh_deadline(owner, control.time.monotonic(), healthy_fault)
    assert owner._fault_token is not healthy_fault
    assert not owner._suppress
    assert owner._deadline == 0.0
    assert owner.input_ledger._blocked


def test_fault_just_before_suppression_write_cannot_be_overwritten():
    class InterruptedCoordinator(control.ControlCoordinator):
        def __setattr__(self, name, value):
            if name == "_suppress" and value is True and getattr(self, "interrupt_write", False):
                self.interrupt_write = False
                self._fail_open()
            # Model the previously authorized write racing AFTER fail-open.
            super().__setattr__(name, value)

    owner = InterruptedCoordinator()
    owner._state = "ai"
    owner._visual_armed = True
    healthy_fault = owner._fault_token
    owner.interrupt_write = True
    control_worker.refresh_deadline(owner, control.time.monotonic(), healthy_fault)
    assert owner._fault_token is not healthy_fault
    assert owner._emergency
    assert not owner._suppress
    assert owner._deadline == 0.0
    assert owner.input_ledger._blocked


@pytest.mark.parametrize("delay_at", ["release", "enable", "state"])
def test_health_expiring_during_recovery_keeps_input_blocked(monkeypatch, delay_at):
    owner, clock, _ = worker_fixture(monkeypatch)
    owner._fail_open()
    owner._state = "unavailable"
    fault = owner._fault_token
    owner._rehook_completed = fault
    sampled_at = clock[0]
    if delay_at == "release":
        release = owner.input_ledger.release_all

        def slow_release():
            release()
            clock[0] += 0.25

        monkeypatch.setattr(owner.input_ledger, "release_all", slow_release)
    elif delay_at == "enable":
        enable = owner.input_ledger.enable

        def slow_enable():
            enable()
            clock[0] += 0.25

        monkeypatch.setattr(owner.input_ledger, "enable", slow_enable)
    else:
        set_state = owner._set_locked

        def slow_state(state):
            changed = set_state(state)
            if state == "user":
                clock[0] += 0.25
            return changed

        monkeypatch.setattr(owner, "_set_locked", slow_state)
    owner._recover_after_rehook(fault, healthy=True, sampled_at=sampled_at)
    assert owner._emergency and owner.input_ledger._blocked
    assert owner._state == "unavailable"
    assert not owner._suppress


def test_caller_health_without_timestamp_cannot_authorize_recovery():
    owner = control.ControlCoordinator()
    owner._fail_open()
    owner._rehook_completed = owner._fault_token
    owner._recover_after_rehook(owner._fault_token, healthy=True)
    assert owner._emergency and owner.input_ledger._blocked


@pytest.mark.parametrize("age", [0.25, 1.0])
def test_expired_health_cannot_refresh_suppression(monkeypatch, age):
    owner, clock, _ = worker_fixture(monkeypatch)
    owner._state = "ai"
    owner._visual_armed = True
    control_worker.refresh_deadline(owner, clock[0] - age, owner._fault_token)
    assert not owner._suppress
    assert owner._deadline == 0.0


def test_cleanup_waiting_for_ledger_does_not_block_injected_callback(monkeypatch):
    owner = control.ControlCoordinator()
    owner._state = "ai"
    owner._lease_until = control.time.monotonic() + 15
    owner._thread = SimpleNamespace(is_alive=lambda: True)
    owner._thread_beat = control.time.monotonic()
    entered = threading.Event()
    release = owner.input_ledger.release_all

    def observed_release():
        entered.set()
        return release()

    monkeypatch.setattr(owner.input_ledger, "release_all", observed_release)
    monkeypatch.setattr(control._user32, "CallNextHookEx", lambda *args: 0)
    owner._queue(("hotkey",))
    worker = threading.Thread(target=control_worker.run_control_worker, args=(owner, lambda: True))
    event = control._KeyHookData()
    event.vkCode, event.flags = 0xA0, 0x10
    with owner.input_ledger._lock:
        worker.start()
        try:
            assert entered.wait(1), "worker never reached cleanup"
            # The actual hook function is independent of worker business locks.
            assert owner._physical_key(0, 0x100, ctypes.addressof(event)) == 0
            assert worker.is_alive()
        finally:
            owner._stop.set()
            owner._release_requested.set()
    worker.join(1)
    assert not worker.is_alive()
