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

from unittest.mock import MagicMock

import numpy as np
import pytest

from windows_mcp.desktop import screenshot
from windows_mcp.desktop.screenshot import _DxcamBackend


class FakeCamera:
    """Returns queued results for new_frame_only=True; records every call."""

    def __init__(self, new_frames: list, cached=None) -> None:
        self.new_frames = list(new_frames)
        self.cached = cached
        self.calls: list[dict] = []

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
