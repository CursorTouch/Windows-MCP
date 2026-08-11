"""Unit tests for the screenshot capture module.

Tests the ``capture()`` public API and each backend class directly,
without going through ``Desktop.get_screenshot``.
"""

from typing import Tuple
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

import windows_mcp.desktop.screenshot as screenshot
from windows_mcp.desktop.screenshot import (
    DxcamOutput,
    _DxcamBackend,
    _MssBackend,
    _PillowBackend,
    _ScreenshotBackend,
    _get_backend,
    capture,
    get_screenshot_backend,
)
from windows_mcp.uia import Rect

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MONITOR_0 = Rect(0, 0, 1920, 1080)
MONITOR_1 = Rect(1920, 0, 3840, 1080)
TWO_MONITORS = [MONITOR_0, MONITOR_1]
DXGI_OUTPUTS = [
    DxcamOutput(device_idx=0, output_idx=0, rect=MONITOR_0),
    DxcamOutput(device_idx=0, output_idx=1, rect=MONITOR_1),
]


@pytest.fixture(autouse=True)
def _isolate_backend_instances(monkeypatch):
    """Ensure each test gets a fresh backend instance pool."""
    monkeypatch.setattr(screenshot, "_backend_instances", {})


# ---------------------------------------------------------------------------
# TestBackendRegistry
# ---------------------------------------------------------------------------


class TestBackendRegistry:
    def test_builtin_backends_are_registered(self):
        reg = _ScreenshotBackend.registry
        assert "dxcam" in reg
        assert "mss" in reg
        assert "pillow" in reg

    def test_subclass_with_name_and_priority_is_registered(self):
        class _Dummy(_ScreenshotBackend):
            name = "_test_dummy"
            priority = 999

            def capture(self, capture_rect):
                return Image.new("RGB", (1, 1))

        assert _ScreenshotBackend.registry["_test_dummy"] is _Dummy
        # Clean up so it doesn't leak to other tests.
        _ScreenshotBackend.registry.pop("_test_dummy", None)

    def test_subclass_without_priority_is_not_registered(self):
        class _Incomplete(_ScreenshotBackend):
            name = "_test_incomplete"

        assert "_test_incomplete" not in _ScreenshotBackend.registry

    def test_inherited_subclass_does_not_overwrite_registry(self):
        """A subclass that inherits name/priority without overriding should not register."""
        original = _ScreenshotBackend.registry.get("pillow")

        class _ChildPillow(_PillowBackend):
            pass  # inherits name="pillow", priority=100 but doesn't define them in __dict__

        assert _ScreenshotBackend.registry["pillow"] is original

    def test_duplicate_name_raises_value_error(self):
        with pytest.raises(ValueError, match="Duplicate screenshot backend name"):

            class _Conflict(_ScreenshotBackend):
                name = "pillow"
                priority = 50

    def test_auto_chain_respects_priority_order(self):
        classes = sorted(_ScreenshotBackend.registry.values(), key=lambda c: c.priority)
        names = [c.name for c in classes]
        assert names.index("dxcam") < names.index("mss") < names.index("pillow")


# ---------------------------------------------------------------------------
# TestGetScreenshotBackend
# ---------------------------------------------------------------------------


class TestGetScreenshotBackend:
    @pytest.mark.parametrize("value", ["auto", "pillow", "dxcam", "mss"])
    def test_valid_values_returned_as_is(self, monkeypatch, value):
        monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_BACKEND", value)
        assert get_screenshot_backend() == value

    def test_invalid_value_falls_back_to_auto(self, monkeypatch):
        monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_BACKEND", "bogus")
        assert get_screenshot_backend() == "auto"

    def test_default_is_auto(self, monkeypatch):
        monkeypatch.delenv("WINDOWS_MCP_SCREENSHOT_BACKEND", raising=False)
        assert get_screenshot_backend() == "auto"

    def test_whitespace_and_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_BACKEND", "  Pillow  ")
        assert get_screenshot_backend() == "pillow"


# ---------------------------------------------------------------------------
# TestDxcamBackend
# ---------------------------------------------------------------------------


class TestDxcamBackend:
    def test_is_available_false_when_dxcam_is_none(self, monkeypatch):
        monkeypatch.setattr(screenshot, "dxcam", None)
        backend = _DxcamBackend()
        assert backend.is_available(MONITOR_0) is False

    def test_is_available_false_when_capture_rect_is_none(self, monkeypatch):
        monkeypatch.setattr(screenshot, "dxcam", MagicMock())
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: DXGI_OUTPUTS))
        backend = _DxcamBackend()
        assert backend.is_available(None) is False

    def test_is_available_false_for_cross_output_rect(self, monkeypatch):
        monkeypatch.setattr(screenshot, "dxcam", MagicMock())
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: DXGI_OUTPUTS))
        backend = _DxcamBackend()
        assert backend.is_available(Rect(0, 0, 3840, 1080)) is False

    def test_is_available_true_for_single_output_rect(self, monkeypatch):
        monkeypatch.setattr(screenshot, "dxcam", MagicMock())
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: DXGI_OUTPUTS))
        backend = _DxcamBackend()
        assert backend.is_available(MONITOR_1) is True

    def test_resolve_region_exact_output_match(self, monkeypatch):
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: DXGI_OUTPUTS))
        result = _DxcamBackend._resolve_region(MONITOR_1)
        assert result == (0, 1, None)

    def test_resolve_region_uses_dxgi_geometry_when_output_order_differs(self, monkeypatch):
        dxgi_outputs = [
            DxcamOutput(device_idx=0, output_idx=0, rect=MONITOR_1),
            DxcamOutput(device_idx=0, output_idx=1, rect=MONITOR_0),
        ]
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: dxgi_outputs))

        result = _DxcamBackend._resolve_region(MONITOR_1)

        assert result == (0, 0, None)

    def test_resolve_region_sub_region_coordinates(self, monkeypatch):
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: DXGI_OUTPUTS))
        sub_rect = Rect(2000, 100, 2500, 500)
        result = _DxcamBackend._resolve_region(sub_rect)
        assert result is not None
        device_idx, output_idx, region = result
        assert device_idx == 0
        assert output_idx == 1
        # Coordinates should be relative to monitor_1's origin (1920, 0).
        assert region == (2000 - 1920, 100 - 0, 2500 - 1920, 500 - 0)

    def test_resolve_region_returns_none_for_cross_output_rect(self, monkeypatch):
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: DXGI_OUTPUTS))
        assert _DxcamBackend._resolve_region(Rect(0, 0, 3840, 1080)) is None

    def test_get_camera_caches_instance(self, monkeypatch):
        fake_camera = MagicMock()
        fake_dxcam = MagicMock()
        fake_dxcam.create.return_value = fake_camera
        monkeypatch.setattr(screenshot, "dxcam", fake_dxcam)

        backend = _DxcamBackend()
        cam1 = backend._get_camera(0, 1)
        cam2 = backend._get_camera(0, 1)
        assert cam1 is cam2
        fake_dxcam.create.assert_called_once_with(
            device_idx=0,
            output_idx=1,
            processor_backend="numpy",
        )

    def test_capture_returns_image(self, monkeypatch):
        fake_camera = MagicMock()
        fake_camera.grab.return_value = [[[255, 0, 0]]]
        fake_dxcam = MagicMock()
        fake_dxcam.create.return_value = fake_camera
        monkeypatch.setattr(screenshot, "dxcam", fake_dxcam)
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: DXGI_OUTPUTS))

        fake_image = Image.new("RGB", (100, 100), "red")
        with patch.object(Image, "fromarray", return_value=fake_image):
            backend = _DxcamBackend()
            result = backend.capture(MONITOR_1)

        assert isinstance(result, Image.Image)
        assert result.size == (100, 100)
        assert result.getbbox() is not None
        fake_dxcam.create.assert_called_once_with(
            device_idx=0,
            output_idx=1,
            processor_backend="numpy",
        )

    def test_capture_uses_output_relative_region_for_sub_region(self, monkeypatch):
        fake_camera = MagicMock()
        fake_camera.grab.return_value = [[[255, 0, 0]]]
        fake_dxcam = MagicMock()
        fake_dxcam.create.return_value = fake_camera
        monkeypatch.setattr(screenshot, "dxcam", fake_dxcam)
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: DXGI_OUTPUTS))

        fake_image = Image.new("RGB", (100, 100), "red")
        with patch.object(Image, "fromarray", return_value=fake_image):
            backend = _DxcamBackend()
            result = backend.capture(Rect(2000, 100, 2500, 500))

        assert result.size == (100, 100)
        fake_camera.grab.assert_called_once_with(
            region=(80, 100, 580, 500),
            copy=True,
            new_frame_only=False,
        )

    def test_capture_raises_on_none_frame(self, monkeypatch):
        fake_camera = MagicMock()
        fake_camera.grab.return_value = None
        fake_dxcam = MagicMock()
        fake_dxcam.create.return_value = fake_camera
        monkeypatch.setattr(screenshot, "dxcam", fake_dxcam)
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: DXGI_OUTPUTS))

        backend = _DxcamBackend()
        with pytest.raises(RuntimeError, match="no frame"):
            backend.capture(MONITOR_1)


# ---------------------------------------------------------------------------
# TestMssBackend
# ---------------------------------------------------------------------------


class TestMssBackend:
    def test_is_available_false_when_mss_is_none(self, monkeypatch):
        monkeypatch.setattr(screenshot, "mss", None)
        assert _MssBackend().is_available(MONITOR_0) is False

    def test_is_available_true_when_mss_exists(self, monkeypatch):
        monkeypatch.setattr(screenshot, "mss", MagicMock())
        assert _MssBackend().is_available(MONITOR_0) is True

    def _make_fake_mss(self, width: int, height: int) -> Tuple[MagicMock, MagicMock]:
        fake_shot = MagicMock()
        fake_shot.size = (width, height)
        fake_shot.rgb = b"\xff\x00\x00" * (width * height)
        fake_sct = MagicMock()
        fake_sct.grab.return_value = fake_shot
        fake_sct.monitors = [{"left": 0, "top": 0, "width": width, "height": height}]
        fake_ctx = MagicMock()
        fake_ctx.__enter__ = MagicMock(return_value=fake_sct)
        fake_ctx.__exit__ = MagicMock(return_value=False)
        fake_module = MagicMock()
        fake_module.mss.return_value = fake_ctx
        return fake_module, fake_sct

    def test_capture_with_rect_builds_correct_monitor(self, monkeypatch):
        fake_module, fake_sct = self._make_fake_mss(500, 400)
        monkeypatch.setattr(screenshot, "mss", fake_module)
        monkeypatch.setattr(
            "windows_mcp.desktop.screenshot.uia.GetVirtualScreenRect",
            lambda: (0, 0, 1920, 1080),
        )

        backend = _MssBackend()
        result = backend.capture(Rect(100, 200, 600, 600))

        assert isinstance(result, Image.Image)
        call_args = fake_sct.grab.call_args[0][0]
        assert call_args == {"left": 100, "top": 200, "width": 500, "height": 400}

    def test_capture_with_non_origin_rect_returns_correct_content(self, monkeypatch):
        """Verify mss capture of a non-primary monitor region is not corrupted by double-crop."""
        width, height = 1920, 1080
        fake_module, _ = self._make_fake_mss(width, height)
        monkeypatch.setattr(screenshot, "mss", fake_module)
        monkeypatch.setattr(
            "windows_mcp.desktop.screenshot.uia.GetVirtualScreenRect",
            lambda: (0, 0, 3840, 1080),
        )

        # capture_rect on second monitor — image origin is (0,0), not (1920,0)
        result = _MssBackend().capture(Rect(1920, 0, 3840, 1080))

        assert result.size == (width, height)
        # The image should retain actual content, not be black from out-of-bounds crop.
        assert result.getbbox() is not None

    def test_capture_without_rect_uses_full_screen(self, monkeypatch):
        fake_module, fake_sct = self._make_fake_mss(1920, 1080)
        monkeypatch.setattr(screenshot, "mss", fake_module)

        backend = _MssBackend()
        result = backend.capture(None)

        assert isinstance(result, Image.Image)
        assert result.size == (1920, 1080)
        # Should have used monitors[0]
        call_args = fake_sct.grab.call_args[0][0]
        assert call_args == fake_sct.monitors[0]


# ---------------------------------------------------------------------------
# TestPillowBackend
# ---------------------------------------------------------------------------


class TestPillowBackend:
    def test_is_available_always_true(self):
        assert _PillowBackend().is_available(None) is True
        assert _PillowBackend().is_available(MONITOR_0) is True

    def test_capture_returns_image_with_correct_size(self, monkeypatch):
        fake_img = Image.new("RGB", (1920, 1080), "blue")
        monkeypatch.setattr(
            screenshot, "ImageGrab", MagicMock(grab=MagicMock(return_value=fake_img))
        )
        monkeypatch.setattr(
            "windows_mcp.desktop.screenshot.uia.GetVirtualScreenRect",
            lambda: (0, 0, 1920, 1080),
        )

        result = _PillowBackend().capture(None)
        assert result.size == (1920, 1080)
        assert result.getbbox() is not None

    def test_capture_with_non_origin_rect_returns_correct_content(self, monkeypatch):
        """Verify pillow capture of a non-primary monitor region is not corrupted by double-crop."""
        fake_img = Image.new("RGB", (1920, 1080), "blue")
        monkeypatch.setattr(
            screenshot, "ImageGrab", MagicMock(grab=MagicMock(return_value=fake_img))
        )
        monkeypatch.setattr(
            "windows_mcp.desktop.screenshot.uia.GetVirtualScreenRect",
            lambda: (0, 0, 3840, 1080),
        )

        # capture_rect on second monitor — ImageGrab.grab(bbox=...) already returns the region
        result = _PillowBackend().capture(Rect(1920, 0, 3840, 1080))

        assert result.size == (1920, 1080)
        # The image should retain actual content, not be black from out-of-bounds crop.
        assert result.getbbox() is not None

    def test_capture_falls_back_on_grab_error_with_rect(self, monkeypatch):
        full_screen = Image.new("RGB", (3840, 1080), "green")
        call_count = {"n": 0}

        def fake_grab(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("grab failed")
            return full_screen

        monkeypatch.setattr(screenshot, "ImageGrab", MagicMock(grab=fake_grab))
        monkeypatch.setattr(
            "windows_mcp.desktop.screenshot.uia.GetVirtualScreenRect",
            lambda: (0, 0, 3840, 1080),
        )

        result = _PillowBackend().capture(Rect(1920, 0, 3840, 1080))
        assert isinstance(result, Image.Image)
        # Should have fallen back and cropped
        assert result.size == (1920, 1080)

    def test_capture_falls_back_to_primary_on_grab_error_without_rect(self, monkeypatch):
        primary = Image.new("RGB", (1920, 1080), "red")
        calls: list[dict[str, object]] = []

        def fake_grab(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise OSError("grab failed")
            return primary

        monkeypatch.setattr(screenshot, "ImageGrab", MagicMock(grab=fake_grab))

        result = _PillowBackend().capture(None)
        assert result.size == (1920, 1080)
        assert result.getbbox() is not None
        assert calls == [{"all_screens": True}, {}]


# ---------------------------------------------------------------------------
# TestCapture (public API)
# ---------------------------------------------------------------------------


class TestCapture:
    def test_get_backend_caches_instance_by_name(self):
        backend1 = _get_backend("pillow")
        backend2 = _get_backend("pillow")

        assert backend1 is backend2
        assert isinstance(backend1, _PillowBackend)

    def test_explicit_pillow_returns_image_and_name(self, monkeypatch):
        fake_img = Image.new("RGB", (1920, 1080), "white")
        monkeypatch.setattr(
            screenshot, "ImageGrab", MagicMock(grab=MagicMock(return_value=fake_img))
        )

        image, backend_name = capture(None, backend="pillow")
        assert backend_name == "pillow"
        assert isinstance(image, Image.Image)
        assert image.size == (1920, 1080)

    def test_unknown_backend_raises_value_error(self):
        with pytest.raises(ValueError, match="nonexistent"):
            capture(None, backend="nonexistent")

    def test_auto_skips_dxcam_when_capture_rect_is_none(self, monkeypatch):
        monkeypatch.setattr(screenshot, "dxcam", MagicMock())
        monkeypatch.setattr(screenshot, "mss", None)
        fake_img = Image.new("RGB", (1920, 1080), "white")
        monkeypatch.setattr(
            screenshot, "ImageGrab", MagicMock(grab=MagicMock(return_value=fake_img))
        )

        image, backend_name = capture(None, backend="auto")
        # dxcam is_available returns False for None rect, mss is None → pillow
        assert backend_name == "pillow"

    def test_auto_falls_back_when_all_unavailable(self, monkeypatch):
        monkeypatch.setattr(screenshot, "dxcam", None)
        monkeypatch.setattr(screenshot, "mss", None)
        fake_img = Image.new("RGB", (800, 600), "blue")
        monkeypatch.setattr(
            screenshot, "ImageGrab", MagicMock(grab=MagicMock(return_value=fake_img))
        )

        image, backend_name = capture(None, backend="auto")
        assert backend_name == "pillow"
        assert image.size == (800, 600)
        assert image.getbbox() is not None

    def test_backend_exception_triggers_fallback(self, monkeypatch):
        """When an available backend's capture() raises, fall through to the next."""
        monkeypatch.setattr(screenshot, "dxcam", None)

        # Make mss available but its capture raises
        fake_mss = MagicMock()
        fake_mss.mss.return_value.__enter__ = MagicMock(side_effect=OSError("mss broken"))
        monkeypatch.setattr(screenshot, "mss", fake_mss)

        fake_img = Image.new("RGB", (1920, 1080), "white")
        monkeypatch.setattr(
            screenshot, "ImageGrab", MagicMock(grab=MagicMock(return_value=fake_img))
        )

        image, backend_name = capture(MONITOR_0, backend="auto")
        # mss failed → pillow fallback
        assert backend_name == "pillow"
        assert isinstance(image, Image.Image)

    def test_explicit_backend_exception_triggers_pillow_fallback(self, monkeypatch):
        """Even an explicitly selected backend falls back to pillow on capture failure."""
        monkeypatch.setattr(screenshot, "dxcam", None)

        fake_mss = MagicMock()
        fake_mss.mss.return_value.__enter__ = MagicMock(side_effect=OSError("mss broken"))
        monkeypatch.setattr(screenshot, "mss", fake_mss)

        fake_img = Image.new("RGB", (1920, 1080), "white")
        monkeypatch.setattr(
            screenshot, "ImageGrab", MagicMock(grab=MagicMock(return_value=fake_img))
        )

        image, backend_name = capture(MONITOR_0, backend="mss")

        assert backend_name == "pillow"
        assert isinstance(image, Image.Image)
        assert image.size == (1920, 1080)

    def test_explicit_dxcam_cross_output_rect_falls_back_to_pillow(self, monkeypatch):
        monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_BACKEND", "dxcam")
        monkeypatch.setattr(screenshot, "dxcam", MagicMock())
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: DXGI_OUTPUTS))

        fake_img = Image.new("RGB", (3840, 1080), "blue")
        monkeypatch.setattr(
            screenshot, "ImageGrab", MagicMock(grab=MagicMock(return_value=fake_img))
        )

        image, backend_name = capture(Rect(0, 0, 3840, 1080), backend="dxcam")

        assert backend_name == "pillow"
        assert isinstance(image, Image.Image)
        assert image.size == (3840, 1080)

    def test_auto_falls_back_when_dxcam_create_loses_output(self, monkeypatch):
        fake_dxcam = MagicMock()
        fake_dxcam.create.side_effect = IndexError("list index out of range")
        monkeypatch.setattr(screenshot, "dxcam", fake_dxcam)
        monkeypatch.setattr(screenshot, "mss", None)
        monkeypatch.setattr(_DxcamBackend, "_iter_outputs", staticmethod(lambda: DXGI_OUTPUTS))

        fake_img = Image.new("RGB", (1920, 1080), "blue")
        monkeypatch.setattr(
            screenshot, "ImageGrab", MagicMock(grab=MagicMock(return_value=fake_img))
        )

        image, backend_name = capture(MONITOR_1, backend="auto")

        assert backend_name == "pillow"
        assert image.size == (1920, 1080)

    def test_capture_returns_non_empty_image(self, monkeypatch):
        """Verify the returned image has actual pixel content (not all-zero)."""
        colored_img = Image.new("RGB", (640, 480), (255, 128, 0))
        monkeypatch.setattr(
            screenshot, "ImageGrab", MagicMock(grab=MagicMock(return_value=colored_img))
        )

        image, _ = capture(None, backend="pillow")
        assert image.getbbox() is not None
        # Spot-check a pixel
        r, g, b = image.getpixel((0, 0))
        assert (r, g, b) == (255, 128, 0)
