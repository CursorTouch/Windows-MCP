"""DXGI captures must show the screen as it is, not the frame before the last change.

dxcam's DXGI path polls AcquireNextFrame with a zero timeout. With
new_frame_only=False it falls back to its cached frame whenever the newest
composed frame has not reached the duplication output yet. Right after a click,
that cached frame still showed the AI indicator (hidden for the capture) and the
previous screenshot's region flash: measured on a Windows Server VM, 6 of 25
captures held the indicator and 24 of 25 the previous flash. The backend now
flushes composition, waits briefly for a new frame, and otherwise reuses the
last full frame it saw itself.
"""

import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from windows_mcp.desktop import screenshot
from windows_mcp.desktop.screenshot import DxcamOutput, _DxcamBackend
from windows_mcp.uia import Rect


class FakeCamera:
    """Returns queued results for new_frame_only=True; records every call."""

    def __init__(self, new_frames: list, cached=None) -> None:
        self.new_frames = list(new_frames)
        self.cached = cached
        self.calls: list[dict] = []
        self.releases = 0

    def release(self) -> None:
        self.releases += 1

    def grab(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("new_frame_only"):
            return self.new_frames.pop(0) if self.new_frames else None
        return self.cached


def _frame(value: int) -> np.ndarray:
    frame = np.zeros((40, 60, 3), dtype=np.uint8)
    frame[:, :] = value
    frame[10, 20] = (1, 2, 3)  # marks the crop origin used below
    return frame


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> _DxcamBackend:
    instance = _DxcamBackend()
    monkeypatch.setattr(screenshot, "_dwm_flush", MagicMock())
    monkeypatch.setattr(screenshot.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(instance, "_resolve_region", lambda rect: (0, 0, (20, 10, 50, 30)))
    monkeypatch.setattr(
        instance,
        "_iter_outputs",
        lambda: [DxcamOutput(device_idx=0, output_idx=0, rect=Rect(0, 0, 60, 40))],
    )
    return instance


def _use(backend: _DxcamBackend, camera: FakeCamera) -> None:
    backend._camera_cache[(0, 0)] = camera


def test_waits_for_a_new_frame_and_crops_it(backend: _DxcamBackend) -> None:
    camera = FakeCamera([None, None, _frame(200)])
    _use(backend, camera)

    image = backend.capture(object())

    assert image.size == (30, 20)
    assert image.getpixel((0, 0)) == (1, 2, 3)
    assert screenshot._dwm_flush.called
    assert all(call == {"copy": True, "new_frame_only": True} for call in camera.calls)


def test_reuses_its_own_last_frame_not_dxcams_older_cache(
    backend: _DxcamBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    camera = FakeCamera([_frame(200)], cached=_frame(10))
    _use(backend, camera)
    backend.capture(object())

    # Nothing changes on screen: no new frame arrives within the wait.
    monkeypatch.setattr(screenshot, "_NEW_FRAME_WAIT_SECONDS", 0)
    image = backend.capture(object())

    assert image.getpixel((5, 5)) == (200, 200, 200)
    assert {"copy": True, "new_frame_only": False} not in camera.calls


def test_first_capture_without_a_new_frame_uses_dxcams_cache(
    backend: _DxcamBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screenshot, "_NEW_FRAME_WAIT_SECONDS", 0)
    camera = FakeCamera([], cached=_frame(90))
    _use(backend, camera)

    image = backend.capture(object())

    assert image.getpixel((5, 5)) == (90, 90, 90)


def test_no_frame_at_all_raises_so_the_next_backend_runs(
    backend: _DxcamBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screenshot, "_NEW_FRAME_WAIT_SECONDS", 0)
    _use(backend, FakeCamera([], cached=None))

    with pytest.raises(RuntimeError, match="DXGI capture returned no frame"):
        backend.capture(object())


def test_full_output_capture_returns_the_whole_frame(
    backend: _DxcamBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backend, "_resolve_region", lambda rect: (0, 0, None))
    _use(backend, FakeCamera([_frame(200)]))

    image = backend.capture(object())

    assert image.size == (60, 40)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(backend: _DxcamBackend) -> Clock:
    fake = Clock()
    backend._clock = fake
    return fake


def _install_dxcam(monkeypatch: pytest.MonkeyPatch, *cameras: FakeCamera) -> list:
    queue = list(cameras)
    created: list = []

    def create(**kwargs):
        created.append(kwargs)
        return queue.pop(0)

    monkeypatch.setattr(screenshot, "dxcam", SimpleNamespace(create=create))
    return created


def test_last_frame_is_kept_within_the_window(
    backend: _DxcamBackend, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    camera = FakeCamera([_frame(200)], cached=_frame(10))
    _use(backend, camera)
    backend.capture(object())

    clock.now += screenshot._LAST_FRAME_TTL_SECONDS - 0.5
    backend.release_expired()
    monkeypatch.setattr(screenshot, "_NEW_FRAME_WAIT_SECONDS", 0)
    image = backend.capture(object())

    assert camera.releases == 0
    assert image.getpixel((5, 5)) == (200, 200, 200)


def test_expired_frame_releases_its_camera_and_the_next_capture_starts_fresh(
    backend: _DxcamBackend, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = FakeCamera([_frame(200)], cached=_frame(10))
    _use(backend, old)
    backend.capture(object())

    clock.now += screenshot._LAST_FRAME_TTL_SECONDS + 0.1
    backend.release_expired()
    assert old.releases == 1

    # The screen changed while idle. A new duplication hands out the current
    # desktop as its first frame, so the capture shows it, not the old picture.
    fresh = FakeCamera([_frame(70)])
    created = _install_dxcam(monkeypatch, fresh)
    old_calls = len(old.calls)
    image = backend.capture(object())

    assert image.getpixel((5, 5)) == (70, 70, 70)
    assert len(created) == 1
    assert len(old.calls) == old_calls
    assert old.releases == 1


def test_after_expiry_a_capture_with_no_frame_fails_instead_of_showing_an_old_one(
    backend: _DxcamBackend, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(backend, FakeCamera([_frame(200)], cached=_frame(10)))
    backend.capture(object())
    clock.now += screenshot._LAST_FRAME_TTL_SECONDS + 0.1
    backend.release_expired()

    monkeypatch.setattr(screenshot, "_NEW_FRAME_WAIT_SECONDS", 0)
    _install_dxcam(monkeypatch, FakeCamera([], cached=None))

    # Raising lets screenshot.capture() move on to the next backend.
    with pytest.raises(RuntimeError, match="DXGI capture returned no frame"):
        backend.capture(object())


def test_an_idle_backend_releases_its_camera_without_another_capture(
    backend: _DxcamBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The server sits idle between tool calls; the frame must not wait for the
    # next capture to be dropped.
    monkeypatch.setattr(screenshot, "_LAST_FRAME_TTL_SECONDS", 0.05)
    camera = FakeCamera([_frame(200)])
    _use(backend, camera)
    backend.capture(object())

    deadline = time.monotonic() + 3
    while camera.releases == 0 and time.monotonic() < deadline:
        threading.Event().wait(0.01)

    assert camera.releases == 1


def test_a_camera_whose_capture_failed_is_still_released_when_idle(
    backend: _DxcamBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screenshot, "_LAST_FRAME_TTL_SECONDS", 0.05)
    monkeypatch.setattr(screenshot, "_NEW_FRAME_WAIT_SECONDS", 0)
    camera = FakeCamera([], cached=None)
    _use(backend, camera)
    with pytest.raises(RuntimeError):
        backend.capture(object())

    deadline = time.monotonic() + 3
    while camera.releases == 0 and time.monotonic() < deadline:
        threading.Event().wait(0.01)

    assert camera.releases == 1


def test_a_camera_is_never_released_while_a_capture_is_using_it(
    backend: _DxcamBackend, clock: Clock
) -> None:
    grabbing = threading.Event()
    finish = threading.Event()

    class SlowCamera(FakeCamera):
        def grab(self, **kwargs):
            grabbing.set()
            finish.wait(5)
            return super().grab(**kwargs)

    camera = SlowCamera([_frame(200)])
    _use(backend, camera)
    capturing = threading.Thread(target=backend.capture, args=(object(),))
    capturing.start()
    assert grabbing.wait(5)
    # The grab stalls long past the expiry window.
    clock.now += screenshot._LAST_FRAME_TTL_SECONDS + 60

    releasing = threading.Thread(target=backend.release_expired)
    releasing.start()
    releasing.join(0.2)
    released_mid_capture = camera.releases

    finish.set()
    capturing.join(5)
    releasing.join(5)

    assert released_mid_capture == 0
    # The capture just used the camera, so it is not expired any more either.
    assert camera.releases == 0


def test_cameras_are_released_once_when_the_display_set_changes(
    backend: _DxcamBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    left = DxcamOutput(device_idx=0, output_idx=0, rect=Rect(0, 0, 60, 40))
    right = DxcamOutput(device_idx=0, output_idx=1, rect=Rect(60, 0, 120, 40))
    outputs = [left, right]
    backend._iter_outputs = lambda: list(outputs)
    target = {"key": (0, 0)}
    monkeypatch.setattr(backend, "_resolve_region", lambda rect: (*target["key"], (20, 10, 50, 30)))
    first, second = FakeCamera([_frame(200)]), FakeCamera([_frame(150)])
    backend._camera_cache[(0, 0)] = first
    backend._camera_cache[(0, 1)] = second
    backend.capture(object())
    target["key"] = (0, 1)
    backend.capture(object())
    assert (first.releases, second.releases) == (0, 0)

    # The right-hand display switches resolution: every cached camera and frame
    # describes the old layout, so all of them go.
    outputs[1] = DxcamOutput(device_idx=0, output_idx=1, rect=Rect(60, 0, 140, 50))
    replacement = FakeCamera([_frame(80), _frame(81)])
    created = _install_dxcam(monkeypatch, replacement)
    image = backend.capture(object())

    assert (first.releases, second.releases) == (1, 1)
    assert created == [{"device_idx": 0, "output_idx": 1, "processor_backend": "numpy"}]
    assert image.getpixel((5, 5)) == (80, 80, 80)

    # Same layout again: nothing more is released.
    backend.capture(object())
    assert (first.releases, second.releases, replacement.releases) == (1, 1, 0)


def test_a_display_that_goes_away_releases_the_cameras(
    backend: _DxcamBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = [
        DxcamOutput(device_idx=0, output_idx=0, rect=Rect(0, 0, 60, 40)),
        DxcamOutput(device_idx=0, output_idx=1, rect=Rect(60, 0, 120, 40)),
    ]
    backend._iter_outputs = lambda: list(outputs)
    camera = FakeCamera([_frame(200)])
    _use(backend, camera)
    backend.capture(object())

    outputs.pop()
    _install_dxcam(monkeypatch, FakeCamera([_frame(90)]))
    backend.capture(object())

    assert camera.releases == 1


@pytest.mark.parametrize("value", [None, "", "  "])
def test_idle_window_defaults_to_thirty_seconds(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("WINDOWS_MCP_DXCAM_IDLE_SECONDS", raising=False)
    else:
        monkeypatch.setenv("WINDOWS_MCP_DXCAM_IDLE_SECONDS", value)

    with caplog.at_level("WARNING", logger=screenshot.__name__):
        assert screenshot.get_dxcam_idle_seconds() == 30.0
    assert caplog.text == ""


def test_idle_window_can_be_set_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINDOWS_MCP_DXCAM_IDLE_SECONDS", " 2.5 ")

    assert screenshot.get_dxcam_idle_seconds() == 2.5


@pytest.mark.parametrize("value", ["soon", "0", "-4", "nan", "inf"])
def test_an_invalid_idle_window_falls_back_to_the_default_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
) -> None:
    monkeypatch.setenv("WINDOWS_MCP_DXCAM_IDLE_SECONDS", value)

    with caplog.at_level("WARNING", logger=screenshot.__name__):
        assert screenshot.get_dxcam_idle_seconds() == 30.0
    assert "WINDOWS_MCP_DXCAM_IDLE_SECONDS" in caplog.text


@pytest.mark.parametrize("value", ["1e7", "1e300"])
def test_an_idle_window_beyond_the_timer_maximum_is_clamped_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
) -> None:
    monkeypatch.setenv("WINDOWS_MCP_DXCAM_IDLE_SECONDS", value)

    with caplog.at_level("WARNING", logger=screenshot.__name__):
        seconds = screenshot.get_dxcam_idle_seconds()
    assert seconds == threading.TIMEOUT_MAX
    assert "WINDOWS_MCP_DXCAM_IDLE_SECONDS" in caplog.text

    # A timer armed with the clamped window must keep waiting; an unclamped
    # one dies at once with OverflowError and idle expiry would stop.
    timer = threading.Timer(seconds, lambda: None)
    timer.daemon = True
    timer.start()
    try:
        timer.join(0.2)
        assert timer.is_alive()
    finally:
        timer.cancel()
        timer.join(timeout=5)
    assert not timer.is_alive()


class FakeTimer:
    """Stands in for threading.Timer; records whether it was started or cancelled."""

    instances: list["FakeTimer"] = []

    def __init__(self, delay: float, function) -> None:
        self.delay = delay
        self.function = function
        self.daemon = False
        self.started = False
        self.cancelled = False
        FakeTimer.instances.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


@pytest.fixture
def fake_timers(monkeypatch: pytest.MonkeyPatch) -> list[FakeTimer]:
    FakeTimer.instances = []
    monkeypatch.setattr(screenshot.threading, "Timer", FakeTimer)
    return FakeTimer.instances


def test_shutdown_cancels_the_idle_timer_and_releases_the_camera(
    backend: _DxcamBackend, fake_timers: list[FakeTimer]
) -> None:
    camera = FakeCamera([_frame(200)])
    _use(backend, camera)
    backend.capture(object())
    assert [t.started for t in fake_timers] == [True]

    backend.shutdown()

    assert fake_timers[0].cancelled
    assert camera.releases == 1


def test_shutdown_twice_releases_once(backend: _DxcamBackend, fake_timers: list[FakeTimer]) -> None:
    camera = FakeCamera([_frame(200)])
    _use(backend, camera)
    backend.capture(object())

    backend.shutdown()
    backend.shutdown()

    assert camera.releases == 1


def test_an_idle_timer_that_fires_after_shutdown_does_not_rearm(
    backend: _DxcamBackend, fake_timers: list[FakeTimer]
) -> None:
    _use(backend, FakeCamera([_frame(200)]))
    backend.capture(object())
    backend.shutdown()

    fake_timers[0].function()  # a timer already running when cancel() came

    assert len(fake_timers) == 1


def test_module_shutdown_is_safe_when_dxcam_was_never_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(screenshot, "_backend_instances", {})

    screenshot.shutdown_dxcam()
    screenshot.shutdown_dxcam()


def test_module_shutdown_shuts_down_the_cached_dxcam_backend(
    monkeypatch: pytest.MonkeyPatch, backend: _DxcamBackend, fake_timers: list[FakeTimer]
) -> None:
    camera = FakeCamera([_frame(200)])
    _use(backend, camera)
    backend.capture(object())
    monkeypatch.setattr(screenshot, "_backend_instances", {"dxcam": backend})

    screenshot.shutdown_dxcam()

    assert fake_timers[0].cancelled
    assert camera.releases == 1


def test_cameras_are_released_when_the_interpreter_exits() -> None:
    child = textwrap.dedent(
        """
        from windows_mcp.desktop import screenshot

        class MarkerCamera:
            def release(self):
                print("CAMERA-RELEASED", flush=True)

        backend = screenshot._DxcamBackend()
        backend._camera_cache[(0, 0)] = MarkerCamera()
        screenshot._backend_instances[screenshot._DxcamBackend.name] = backend
        print("EXITING", flush=True)
        """
    )
    src = Path(screenshot.__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(src), os.environ.get("PYTHONPATH", "")]),
    }

    result = subprocess.run(
        [sys.executable, "-c", child], capture_output=True, text=True, env=env, timeout=60
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["EXITING", "CAMERA-RELEASED"]
    assert result.stderr.strip() == ""
