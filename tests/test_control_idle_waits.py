"""Control threads block while nothing needs them, instead of polling."""

import ctypes
import threading
import time
from types import SimpleNamespace

from windows_mcp.desktop import control, control_overlay, control_win32, control_worker

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_kernel32.WaitForSingleObject.restype = ctypes.c_uint32


def _signaled(handle) -> bool:
    return _kernel32.WaitForSingleObject(handle, 0) == 0


def _pump_user32(on_idle):
    hooks = iter(range(100, 200))
    return SimpleNamespace(
        SetWindowsHookExW=lambda *args: next(hooks),
        UnhookWindowsHookEx=lambda handle: 1,
        RegisterClassExW=lambda wc: 1,
        CreateWindowExW=lambda *args: 777,
        RegisterRawInputDevices=lambda *args: 1,
        PeekMessageW=lambda *args: 0,
        TranslateMessage=lambda *args: 1,
        DispatchMessageW=lambda *args: 1,
        DefWindowProcW=lambda *args: 0,
        DestroyWindow=lambda hwnd: 1,
        UnregisterClassW=lambda *args: 1,
        GetAsyncKeyState=lambda vk: 0,
        MsgWaitForMultipleObjectsEx=on_idle,
    )


def _run_pump(monkeypatch, owner, waits_wanted, inspect=None):
    clock = [100.0]
    waits = []

    def wait(count, handles, timeout, mask, flags):
        waits.append((count, timeout))
        if inspect is not None:
            inspect(owner)
        clock[0] += 0.05
        if len(waits) >= waits_wanted:
            owner._stop.set()
        return 0

    user32 = _pump_user32(wait)
    monkeypatch.setattr(control, "_user32", user32)
    monkeypatch.setattr(control_win32, "_user32", user32)
    monkeypatch.setattr(control_win32.time, "monotonic", lambda: clock[0])
    control_win32.run_input_monitor(owner)
    assert owner._startup_error is None
    return waits


def test_idle_pump_blocks_until_the_next_hook_rotation(monkeypatch):
    owner = control.ControlCoordinator()
    owner._state = "ready"
    waits = _run_pump(monkeypatch, owner, 3)
    # One wake handle, and no 10 ms polling: only the 5 s hook rotation bounds the wait.
    assert [count for count, _ in waits] == [1, 1, 1]
    assert [timeout for _, timeout in waits] == [5000, 4950, 4900]


def test_pump_samples_tracked_holds_at_the_recovery_interval(monkeypatch):
    owner = control.ControlCoordinator()
    owner._state = "user"
    owner._last_user = owner._last_physical_event = 99.0
    owner._pressed.add(0x41)
    waits = _run_pump(monkeypatch, owner, 2)
    assert [timeout for _, timeout in waits] == [100, 100]


def test_other_threads_can_wake_the_blocked_pump(monkeypatch):
    seen = []

    def inspect(owner):
        handle = owner._pump_event
        assert handle and not _signaled(handle)
        owner._wake_pump()
        seen.append(_signaled(handle))
        owner._fail_open()  # A fault must reach the pump to reinstall hooks.
        seen.append(_signaled(handle))

    owner = control.ControlCoordinator()
    owner._state = "ready"
    _run_pump(monkeypatch, owner, 1, inspect)
    assert seen == [True, True]
    assert owner._pump_event is None  # Closed when the pump exits.


class _Wake:
    """A worker wake event that advances a fake clock by each requested timeout."""

    def __init__(self, owner, clock, limit):
        self.owner, self.clock, self.limit = owner, clock, limit
        self.waits = []

    def set(self):
        pass

    def clear(self):
        pass

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.clock[0] += timeout if timeout is not None else 1000.0
        if len(self.waits) >= self.limit:
            self.owner._stop.set()
        return False


def _worker(monkeypatch, state, limit):
    clock = [100.0]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(control_worker, "time", fake_time)
    monkeypatch.setattr(control, "time", fake_time)
    owner = control.ControlCoordinator()
    owner._thread = SimpleNamespace(is_alive=lambda: True)
    owner._thread_beat = clock[0]
    owner._state = state
    wake = _Wake(owner, clock, limit)
    owner._release_requested = wake
    return owner, clock, wake


def test_ready_worker_wakes_rarely(monkeypatch):
    owner, clock, wake = _worker(monkeypatch, "ready", 30)
    owner._pump_idle = True

    def answer():
        owner._thread_beat = clock[0]

    monkeypatch.setattr(owner, "_wake_pump", answer)
    control_worker.run_control_worker(owner, lambda: True)
    # 30 wakes cover about a minute: no 10 ms polling while nothing can change.
    assert clock[0] - 100.0 >= 55.0
    assert all(timeout >= 1.0 for timeout in wake.waits[1:])
    assert not owner._emergency
    assert owner._state == "ready"


def test_ai_worker_keeps_quarter_second_ticks(monkeypatch):
    owner, _, wake = _worker(monkeypatch, "ai", 8)
    owner._thread_beat = 1e9  # Pump stays fresh.
    control_worker.run_control_worker(owner, lambda: True)
    assert wake.waits and all(0 < timeout <= 0.25 for timeout in wake.waits)


def test_user_wait_wakes_at_the_idle_deadline(monkeypatch):
    owner, clock, wake = _worker(monkeypatch, "user", 4)
    owner.idle_timeout_seconds = 10.0
    owner._last_user = owner._last_physical_event = clock[0] - 7.0
    owner._thread_beat = 1e9
    changes = []
    owner.subscribe(lambda status: changes.append((status["state"], clock[0])))
    control_worker.run_control_worker(owner, lambda: True)
    # The countdown ends exactly when the user has been idle for the timeout.
    assert changes and changes[0][0] == "ready"
    assert abs(changes[0][1] - 103.0) < 1e-6


def test_worker_pings_an_idle_pump_instead_of_failing_on_an_old_beat(monkeypatch):
    owner, clock, _ = _worker(monkeypatch, "ready", 3)
    owner._thread_beat = clock[0] - 4.0  # Pump blocked until its next rotation.
    owner._pump_idle = True
    pings = []

    def answer():
        pings.append(clock[0])
        owner._thread_beat = clock[0]

    monkeypatch.setattr(owner, "_wake_pump", answer)
    control_worker.run_control_worker(owner, lambda: True)
    assert pings
    assert not owner._emergency


def test_unanswered_pump_ping_fails_open(monkeypatch):
    owner, clock, _ = _worker(monkeypatch, "ready", 4)
    owner._thread_beat = clock[0] - 4.0
    owner._pump_idle = True
    pings = []
    monkeypatch.setattr(owner, "_wake_pump", lambda: pings.append(clock[0]))
    control_worker.run_control_worker(owner, lambda: True)
    assert pings
    assert owner._emergency


def test_busy_pump_with_old_beat_fails_open(monkeypatch):
    owner, clock, _ = _worker(monkeypatch, "ready", 2)
    owner._thread_beat = clock[0] - 4.0
    owner._pump_idle = False  # Stuck inside a callback, not parked in its wait.
    control_worker.run_control_worker(owner, lambda: True)
    assert owner._emergency


def test_hidden_indicator_thread_blocks_and_answers_pings():
    control_overlay.stop()
    control_overlay.start()
    try:
        indicator = control_overlay._instance
        deadline = time.monotonic() + 1.0
        while not indicator.idle and time.monotonic() < deadline:
            time.sleep(0.01)
        assert indicator.idle
        beat = indicator.heartbeat
        time.sleep(0.3)
        assert indicator.heartbeat == beat  # No 20 Hz refresh while nothing is shown.
        time.sleep(1.0)
        assert control_overlay.is_healthy() is True  # Old beat, but parked: ping it.
        deadline = time.monotonic() + 1.0
        while indicator.heartbeat == beat and time.monotonic() < deadline:
            time.sleep(0.01)
        assert indicator.heartbeat > beat
        assert control_overlay.is_healthy() is True
    finally:
        control_overlay.stop()


def test_parked_indicator_that_ignores_its_ping_is_unhealthy(monkeypatch):
    now = time.monotonic()

    class Parked:
        thread = threading.current_thread()
        stopping = False
        error = None
        idle = True
        heartbeat = now - 5.0
        ping_at = now - 1.5

        def ping(self):
            pytest_fail = AssertionError("an outstanding ping must not be replaced")
            raise pytest_fail

    monkeypatch.setattr(control_overlay, "_instance", Parked())
    assert control_overlay.is_healthy() is False
    Parked.ping_at = now - 0.2
    assert control_overlay.is_healthy() is True


def test_shown_indicator_with_old_beat_stays_unhealthy(monkeypatch):
    class Stalled:
        thread = threading.current_thread()
        stopping = False
        error = None
        idle = False
        heartbeat = time.monotonic() - 2.0
        ping_at = 0.0

        def ping(self):
            raise AssertionError("a busy owner is not pinged")

    monkeypatch.setattr(control_overlay, "_instance", Stalled())
    assert control_overlay.is_healthy() is False


def test_indicator_change_wakes_a_parked_owner(monkeypatch):
    shown = threading.Event()

    class Layer:
        hwnd = 1
        breathes = True

        def set_opacity(self, opacity):
            pass

        def show(self):
            shown.set()

        def hide(self):
            pass

        def move(self, x, y):
            pass

        def close(self):
            pass

    monkeypatch.setattr(control_overlay, "_monitor_rects", lambda: ((0, 0, 800, 600),))
    monkeypatch.setattr(
        control_overlay,
        "_build_layers",
        lambda rects, pending, beat=None, notice=True: ([Layer()], Layer()),
    )
    monkeypatch.setattr(control_overlay._user32, "GetCursorPos", lambda ptr: True)
    control_overlay.stop()
    control_overlay.start()
    try:
        indicator = control_overlay._instance
        deadline = time.monotonic() + 1.0
        while not indicator.idle and time.monotonic() < deadline:
            time.sleep(0.01)
        started = time.monotonic()
        assert control_overlay.set_active(True)
        assert shown.is_set()
        assert time.monotonic() - started < 1.0
        assert not indicator.idle or indicator.active
    finally:
        control_overlay.stop()
