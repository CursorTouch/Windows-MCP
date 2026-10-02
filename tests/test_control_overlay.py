"""Indicator geometry, lifecycle, and screenshot exclusion checks."""

import threading
import time
from contextlib import contextmanager
from unittest.mock import Mock

from PIL import Image
import pytest

from windows_mcp.desktop import control, control_overlay, flash_overlay
from windows_mcp.desktop.service import Desktop


@pytest.fixture(autouse=True)
def _stop_indicator():
    control_overlay.stop()
    yield
    control_overlay.stop()


def test_capture_suspends_and_restores_including_error(monkeypatch):
    calls = []

    class FakeIndicator:
        def change(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(control_overlay, "_instance", FakeIndicator())
    with pytest.raises(ValueError, match="capture failed"):
        with control_overlay.suspend_for_capture():
            calls.append("capture")
            raise ValueError("capture failed")
    assert calls == [{"suspended": 1}, "capture", {"suspended": -1}]


def test_indicator_lifecycle_and_no_restore_after_takeover(monkeypatch):
    events = []

    class FakeLayer:
        hwnd = 1
        breathes = True

        def set_opacity(self, opacity):
            events.append(("opacity", opacity))

        def show(self):
            events.append("show")

        def hide(self):
            events.append("hide")

        def move(self, x, y):
            pass

        def close(self):
            events.append("close")

    monkeypatch.setattr(control_overlay, "_monitor_rects", lambda: ((0, 0, 800, 600),))
    monkeypatch.setattr(
        control_overlay, "_build_layers", lambda rects, pending: ([FakeLayer()], FakeLayer())
    )
    monkeypatch.setattr(control_overlay._user32, "GetCursorPos", lambda ptr: True)
    monkeypatch.setattr(flash_overlay, "_pump_messages", lambda hwnd: None)
    assert control_overlay.is_healthy() is False
    control_overlay.start()
    assert control_overlay.is_healthy() is True
    control_overlay.set_active(True)
    assert events.count("show") == 2
    with control_overlay.suspend_for_capture():
        assert events.count("hide") == 2
        control_overlay.set_active(False)
    assert events.count("show") == 2
    control_overlay.stop()
    assert control_overlay.is_healthy() is False
    assert events.count("close") == 2


def test_failed_indicator_reports_unhealthy(monkeypatch):
    class FailedIndicator:
        thread = threading.current_thread()
        stopping = False
        error = RuntimeError("window failed")

        def stop(self):
            pass

    monkeypatch.setattr(control_overlay, "_instance", FailedIndicator())
    assert control_overlay.is_healthy() is False


def test_alive_but_stalled_indicator_reports_unhealthy(monkeypatch):
    class StalledIndicator:
        thread = threading.current_thread()  # still alive, but no heartbeat refresh
        stopping = False
        error = None
        heartbeat = time.monotonic() - 2.0

        def stop(self):
            pass

    monkeypatch.setattr(control_overlay, "_instance", StalledIndicator())
    assert control_overlay.is_healthy() is False


def test_capture_ack_timeout_cannot_hide_recovered_indicator(monkeypatch):
    indicator = control_overlay._Indicator()
    indicator.thread = Mock()
    indicator.thread.is_alive.return_value = True
    indicator.heartbeat = time.monotonic()
    indicator.stop = Mock()
    monkeypatch.setattr(control_overlay, "_instance", indicator)
    monkeypatch.setattr(threading.Condition, "wait_for", lambda self, predicate, timeout: False)

    with pytest.raises(RuntimeError, match="did not acknowledge"):
        with control_overlay.suspend_for_capture():
            pytest.fail("Capture must not run before the indicator is hidden")
    assert indicator.suspended == 1
    # Even if the window owner later beats again, the incomplete transition
    # must never be accepted as a healthy, visible AI lease.
    indicator.heartbeat = time.monotonic()
    assert control_overlay.is_healthy() is False
    with pytest.raises(RuntimeError, match="indicator failed"):
        control_overlay.set_active(True)


def test_newer_user_state_rejects_ai_show_waiting_behind_capture(monkeypatch):
    indicator = control_overlay._Indicator()
    indicator.thread = Mock()
    indicator.thread.is_alive.return_value = True
    monkeypatch.setattr(control_overlay, "_instance", indicator)
    stop_ack = threading.Event()

    def acknowledge():
        while not stop_ack.is_set():
            with indicator.condition:
                indicator.condition.wait(timeout=0.01)
                indicator.applied = indicator.version
                indicator.condition.notify_all()

    ack_thread = threading.Thread(target=acknowledge)
    ack_thread.start()
    result = []
    old_ai = threading.Thread(
        target=lambda: result.append(control_overlay.set_active(True, generation=1))
    )
    try:
        with control_overlay._capture_lock:
            old_ai.start()
            assert control_overlay.set_active(False, generation=2)
        old_ai.join(timeout=1.0)
        assert not old_ai.is_alive()
        assert result == [False]
        assert indicator.generation == 2
        assert not indicator.active
    finally:
        stop_ack.set()
        with indicator.condition:
            indicator.condition.notify_all()
        ack_thread.join(timeout=1.0)
        if old_ai.is_alive():
            old_ai.join(timeout=1.0)
        monkeypatch.setattr(control_overlay, "_instance", None)


def test_actual_owner_thread_stall_expires_heartbeat(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def stalled_monitor_lookup():
        entered.set()
        release.wait(timeout=3.0)
        raise RuntimeError("simulated window owner failure")

    monkeypatch.setattr(control_overlay, "_monitor_rects", stalled_monitor_lookup)
    control_overlay.start()
    indicator = control_overlay._instance
    assert indicator is not None
    with indicator.condition:
        indicator.active = True
        indicator.condition.notify_all()
    assert entered.wait(timeout=1.0)
    try:
        time.sleep(1.1)
        assert indicator.thread.is_alive()
        assert control_overlay.is_healthy() is False
    finally:
        release.set()
        control_overlay.stop()


def test_fresh_indicator_heartbeat_reports_healthy(monkeypatch):
    class FreshIndicator:
        thread = threading.current_thread()
        stopping = False
        error = None
        heartbeat = time.monotonic()

        def stop(self):
            pass

    monkeypatch.setattr(control_overlay, "_instance", FreshIndicator())
    assert control_overlay.is_healthy() is True


def test_stop_failure_retains_owner_for_retry(monkeypatch):
    class SlowOwner:
        calls = 0

        def stop(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("join timed out")

    owner = SlowOwner()
    monkeypatch.setattr(control_overlay, "_instance", owner)
    with pytest.raises(RuntimeError, match="join timed out"):
        control_overlay.stop()
    assert control_overlay._instance is owner
    control_overlay.stop()
    assert control_overlay._instance is None
    assert owner.calls == 2


def test_failed_start_keeps_uncleaned_owner_and_prevents_stacking(monkeypatch):
    created = []

    class FailedOwner:
        def __init__(self):
            self.stopping = False
            self.error = None
            self.heartbeat = time.monotonic()
            self.thread = threading.current_thread()
            self.stop_calls = 0
            created.append(self)

        def start(self):
            raise RuntimeError("startup failed")

        def stop(self):
            self.stopping = True
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise RuntimeError("join timed out")

    monkeypatch.setattr(control_overlay, "_Indicator", FailedOwner)
    with pytest.raises(RuntimeError, match="cleanup is pending"):
        control_overlay.start()
    assert control_overlay._instance is created[0]
    with pytest.raises(RuntimeError, match="requires stop"):
        control_overlay.start()
    assert len(created) == 1
    control_overlay.stop()
    assert control_overlay._instance is None


def test_failed_start_clears_owner_after_successful_cleanup(monkeypatch):
    class FailedOwner:
        def start(self):
            raise RuntimeError("startup failed")

        def stop(self):
            pass

    monkeypatch.setattr(control_overlay, "_Indicator", FailedOwner)
    with pytest.raises(RuntimeError, match="startup failed"):
        control_overlay.start()
    assert control_overlay._instance is None


def test_two_captures_are_serialized(monkeypatch):
    active = 0
    peak = 0
    barrier = threading.Barrier(3)

    class FakeIndicator:
        def change(self, **kwargs):
            nonlocal active, peak
            active += kwargs["suspended"]
            peak = max(peak, active)

    monkeypatch.setattr(control_overlay, "_instance", FakeIndicator())

    def capture():
        barrier.wait()
        with control_overlay.suspend_for_capture():
            threading.Event().wait(0.02)

    threads = [threading.Thread(target=capture) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert active == 0
    assert peak == 1


@pytest.mark.parametrize("backend", ["dxcam", "mss", "pillow"])
def test_screenshot_capture_runs_only_while_indicator_hidden(monkeypatch, backend):
    events = []

    @contextmanager
    def suspended():
        events.append("hide")
        try:
            yield
        finally:
            events.append("restore")

    monkeypatch.setattr(control_overlay, "suspend_for_capture", suspended)
    monkeypatch.setattr(
        flash_overlay, "cancel_active_flash", lambda: events.append("cancel flash") or True
    )
    monkeypatch.setattr(
        flash_overlay, "show_capture_flash", lambda rect: events.append("show flash")
    )
    monkeypatch.setattr(
        "windows_mcp.desktop.service.screenshot_capture.capture",
        lambda rect: (events.append("capture") or Image.new("RGB", (2, 2)), backend),
    )
    desktop = Desktop.__new__(Desktop)
    desktop.get_screenshot()
    assert desktop._last_screenshot_backend == backend
    assert events == ["hide", "cancel flash", "capture", "restore", "show flash"]


def test_screenshot_error_restores_indicator_without_flash(monkeypatch):
    events = []

    @contextmanager
    def suspended():
        events.append("hide")
        try:
            yield
        finally:
            events.append("restore")

    monkeypatch.setattr(control_overlay, "suspend_for_capture", suspended)
    monkeypatch.setattr(flash_overlay, "cancel_active_flash", lambda: True)
    monkeypatch.setattr(flash_overlay, "show_capture_flash", lambda rect: events.append("flash"))

    def failing_capture(rect):
        events.append("capture")
        raise OSError("capture failed")

    monkeypatch.setattr("windows_mcp.desktop.service.screenshot_capture.capture", failing_capture)
    with pytest.raises(OSError, match="capture failed"):
        Desktop.__new__(Desktop).get_screenshot()
    assert events == ["hide", "capture", "restore"]


def test_stalled_capture_releases_physical_input_and_keeps_status_available(monkeypatch):
    owner = control.ControlCoordinator()
    with owner._lock:
        owner._set_locked("ready")
    owner.set_health_probe(lambda: True)
    owner.subscribe(
        lambda status: owner.arm_visible(status["generation"]) if status["state"] == "ai" else None
    )
    token = owner.begin_call("Screenshot")
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr("windows_mcp.desktop.service.get_controller", lambda: owner)
    monkeypatch.setattr(flash_overlay, "cancel_active_flash", lambda: True)
    monkeypatch.setattr(flash_overlay, "show_capture_flash", lambda rect: None)

    @contextmanager
    def hidden():
        assert not owner._suppress
        yield

    def capture(rect):
        started.set()
        assert release.wait(2.0)
        return Image.new("RGB", (2, 2)), "test"

    monkeypatch.setattr(control_overlay, "suspend_for_capture", hidden)
    monkeypatch.setattr("windows_mcp.desktop.service.screenshot_capture.capture", capture)
    result = []
    worker = threading.Thread(
        target=lambda: result.append(Desktop.__new__(Desktop).get_screenshot())
    )
    worker.start()
    try:
        assert started.wait(1.0)
        assert not owner._suppress
        assert owner.status()["state"] == "ai"
        with pytest.raises(control.ControlBlocked):
            owner.checkpoint(token)
        owner._handle(("key",))  # Input delivered during the hidden interval wins.
        assert owner.status()["state"] == "user"
    finally:
        release.set()
        worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert result and not owner._suppress


def test_unclosed_flash_prevents_new_capture(monkeypatch):
    events = []

    @contextmanager
    def suspended():
        events.append("hide")
        try:
            yield
        finally:
            events.append("restore")

    monkeypatch.setattr(control_overlay, "suspend_for_capture", suspended)
    monkeypatch.setattr(flash_overlay, "cancel_active_flash", lambda: False)
    monkeypatch.setattr(
        "windows_mcp.desktop.service.screenshot_capture.capture",
        lambda rect: events.append("capture"),
    )
    with pytest.raises(RuntimeError, match="did not close"):
        Desktop.__new__(Desktop).get_screenshot()
    assert events == ["hide", "restore"]
