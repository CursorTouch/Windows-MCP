"""Regression tests for issue #371 — screenshots undecodable on VM/RDP desktops.

The backend chain only ever fell through when a backend *raised*. On VM/RDP
guests dxcam instead initializes cleanly and then returns frames with no pixel
data, so the chain happily returned them and the client received an undecodable
image. The reporter's workaround was to pin WINDOWS_MCP_SCREENSHOT_BACKEND=mss
by hand; these tests cover doing that automatically.
"""

import pytest
from PIL import Image

from windows_mcp.desktop import screenshot as screenshot_mod
from windows_mcp.desktop.screenshot import _is_usable_capture, capture


def good_image(size=(8, 8)) -> Image.Image:
    return Image.new("RGB", size, (10, 20, 30))


class FakeBackend:
    """Stands in for a registered backend without touching the real registry."""

    def __init__(self, name, priority, result=None, available=True, raises=None):
        self.name = name
        self.priority = priority
        self._result = result
        self._available = available
        self._raises = raises
        self.calls = 0

    # `_get_backend` calls the registry entry to build an instance; returning
    # self keeps one object so the test can count calls.
    def __call__(self):
        return self

    def is_available(self, capture_rect):
        return self._available

    def capture(self, capture_rect):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._result


@pytest.fixture
def registry(monkeypatch):
    """Install a clean, isolated backend registry for one test."""

    def install(*backends):
        table = {b.name: b for b in backends}
        monkeypatch.setattr(screenshot_mod._ScreenshotBackend, "registry", table)
        monkeypatch.setattr(screenshot_mod, "_backend_instances", {})
        monkeypatch.setattr(screenshot_mod, "_degraded_backends", set())
        return table

    return install


class TestIsUsableCapture:
    def test_none_is_rejected(self):
        assert _is_usable_capture(None) is False

    def test_zero_sized_frame_is_rejected(self):
        assert _is_usable_capture(Image.new("RGB", (0, 0))) is False

    def test_zero_height_frame_is_rejected(self):
        assert _is_usable_capture(Image.new("RGB", (8, 0))) is False

    def test_ordinary_frame_is_accepted(self):
        assert _is_usable_capture(good_image()) is True

    def test_all_black_frame_is_accepted(self):
        """Content is never judged: a locked or screensaver desktop is legitimate."""
        assert _is_usable_capture(Image.new("RGB", (8, 8), (0, 0, 0))) is True


class TestCaptureFallback:
    def test_working_backend_is_used(self, registry):
        image = good_image()
        dxcam = FakeBackend("dxcam", 10, result=image)
        mss = FakeBackend("mss", 20, result=good_image())
        registry(dxcam, mss, FakeBackend("pillow", 100, result=good_image()))

        result, name = capture(None)
        assert result is image
        assert name == "dxcam"
        assert mss.calls == 0

    def test_empty_frame_falls_through_to_mss(self, registry):
        """The issue #371 case: dxcam succeeds but hands back nothing."""
        mss_image = good_image()
        dxcam = FakeBackend("dxcam", 10, result=Image.new("RGB", (0, 0)))
        mss = FakeBackend("mss", 20, result=mss_image)
        registry(dxcam, mss, FakeBackend("pillow", 100, result=good_image()))

        result, name = capture(None)
        assert name == "mss", "should not have returned dxcam's empty frame"
        assert result is mss_image
        assert dxcam.calls == 1

    def test_none_frame_falls_through_to_mss(self, registry):
        mss = FakeBackend("mss", 20, result=good_image())
        registry(FakeBackend("dxcam", 10, result=None), mss, FakeBackend("pillow", 100))
        assert capture(None)[1] == "mss"

    def test_raising_backend_still_falls_through(self, registry):
        """Pre-existing behaviour must be preserved."""
        mss = FakeBackend("mss", 20, result=good_image())
        registry(
            FakeBackend("dxcam", 10, raises=RuntimeError("DXGI capture returned no frame")),
            mss,
            FakeBackend("pillow", 100),
        )
        assert capture(None)[1] == "mss"

    def test_unavailable_backend_is_skipped(self, registry):
        mss = FakeBackend("mss", 20, result=good_image())
        registry(FakeBackend("dxcam", 10, available=False), mss, FakeBackend("pillow", 100))
        assert capture(None)[1] == "mss"

    def test_backend_order_follows_priority(self, registry):
        registry(
            FakeBackend("pillow", 100, result=good_image()),
            FakeBackend("mss", 20, result=good_image()),
            FakeBackend("dxcam", 10, result=good_image()),
        )
        assert capture(None)[1] == "dxcam"


class TestDegradedLatch:
    def test_bad_backend_is_not_retried(self, registry):
        """Once dxcam is caught returning junk, stop asking it every call."""
        dxcam = FakeBackend("dxcam", 10, result=Image.new("RGB", (0, 0)))
        mss = FakeBackend("mss", 20, result=good_image())
        registry(dxcam, mss, FakeBackend("pillow", 100))

        assert capture(None)[1] == "mss"
        assert capture(None)[1] == "mss"
        assert capture(None)[1] == "mss"

        assert dxcam.calls == 1, "degraded backend should be probed only once"
        assert "dxcam" in screenshot_mod._degraded_backends

    def test_healthy_backend_is_never_latched(self, registry):
        dxcam = FakeBackend("dxcam", 10, result=good_image())
        registry(dxcam, FakeBackend("mss", 20), FakeBackend("pillow", 100))

        capture(None)
        capture(None)
        assert dxcam.calls == 2
        assert screenshot_mod._degraded_backends == set()

    def test_explicitly_selected_backend_is_validated_too(self, registry):
        """Forcing a backend must not bypass the check and return junk."""
        dxcam = FakeBackend("dxcam", 10, result=Image.new("RGB", (0, 0)))
        pillow = FakeBackend("pillow", 100, result=good_image())
        registry(dxcam, FakeBackend("mss", 20, available=False), pillow)

        result, name = capture(None, backend="dxcam")
        assert name == "pillow", "should fall back to the last resort, not return junk"
        assert _is_usable_capture(result)
