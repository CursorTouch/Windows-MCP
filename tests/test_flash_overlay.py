"""Unit tests for the screenshot flash overlay.

The actual Tk window is never created in these tests — they exercise the
public dispatch surface (env-var gating, lifecycle bookkeeping, fallthrough
when ``tkinter`` cannot be imported).
"""

import sys
import threading
from unittest.mock import patch

import pytest

import windows_mcp.desktop.flash_overlay as flash_overlay


@pytest.fixture(autouse=True)
def _reset_active_overlay():
    """Each test starts and ends with no overlay registered."""
    with flash_overlay._lock:
        flash_overlay._active_overlay = None
    yield
    with flash_overlay._lock:
        ov = flash_overlay._active_overlay
        flash_overlay._active_overlay = None
    if ov is not None:
        ov.stop_event.set()


class TestFlashDisabled:
    def test_default_is_enabled(self, monkeypatch):
        monkeypatch.delenv("WINDOWS_MCP_DISABLE_FLASH", raising=False)
        assert flash_overlay._flash_disabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " On "])
    def test_truthy_values_disable(self, monkeypatch, value):
        monkeypatch.setenv("WINDOWS_MCP_DISABLE_FLASH", value)
        assert flash_overlay._flash_disabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_falsy_values_keep_enabled(self, monkeypatch, value):
        monkeypatch.setenv("WINDOWS_MCP_DISABLE_FLASH", value)
        assert flash_overlay._flash_disabled() is False


class _FakeRect:
    """Stand-in for ``uia.Rect`` with the four corner attributes."""

    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class TestShowCaptureFlash:
    def test_disabled_env_var_skips_thread(self, monkeypatch):
        monkeypatch.setenv("WINDOWS_MCP_DISABLE_FLASH", "1")
        with patch.object(threading, "Thread") as fake_thread:
            flash_overlay.show_capture_flash(_FakeRect(0, 0, 100, 100))
        fake_thread.assert_not_called()
        assert flash_overlay._active_overlay is None

    def test_empty_monitor_rects_skips_thread(self, monkeypatch):
        """Full-screen path with zero monitors must not start a thread."""
        monkeypatch.delenv("WINDOWS_MCP_DISABLE_FLASH", raising=False)
        # Patch on the real uia module — ``import windows_mcp.uia as uia`` resolves
        # via the cached parent-package attribute once another test has imported
        # the package, so monkeypatching sys.modules is not reliable on its own.
        import windows_mcp.uia

        monkeypatch.setattr(windows_mcp.uia, "GetMonitorsRect", lambda: [])
        with patch.object(threading, "Thread") as fake_thread:
            flash_overlay.show_capture_flash(None)
        fake_thread.assert_not_called()
        assert flash_overlay._active_overlay is None

    def test_region_capture_passes_single_rect(self, monkeypatch):
        monkeypatch.delenv("WINDOWS_MCP_DISABLE_FLASH", raising=False)

        captured = {}

        class _StubThread:
            def __init__(self, target, args, name, daemon):
                captured["target"] = target
                captured["args"] = args
                captured["name"] = name
                captured["daemon"] = daemon

            def start(self):
                captured["started"] = True

        monkeypatch.setattr(flash_overlay.threading, "Thread", _StubThread)

        flash_overlay.show_capture_flash(_FakeRect(10, 20, 110, 120))

        assert captured["started"] is True
        assert captured["daemon"] is True
        assert captured["name"] == "windows-mcp-flash"
        assert flash_overlay._active_overlay is not None
        rects_arg, full_screen_arg, overlay_arg = captured["args"]
        assert rects_arg == [(10, 20, 110, 120)]
        assert full_screen_arg is False
        assert overlay_arg is flash_overlay._active_overlay

    def test_region_overlay_window_uses_capture_rect_without_expanding(self, monkeypatch):
        calls = {}
        monkeypatch.setattr(
            flash_overlay,
            "_create_layered_window",
            lambda class_name, x, y, width, height: (
                calls.setdefault("window", (class_name, x, y, width, height)) and (1, 2)
            ),
        )
        monkeypatch.setattr(flash_overlay._user32, "ShowWindow", lambda *args: None)
        monkeypatch.setattr(flash_overlay._user32, "SetWindowPos", lambda *args: None)
        monkeypatch.setattr(
            flash_overlay,
            "_render_glow_rgba",
            lambda width, height, rects, *, outward: calls.setdefault(
                "glow", (width, height, rects, outward)
            ),
        )
        monkeypatch.setattr(flash_overlay, "_premultiplied_bgra", lambda image, intensity: b"")
        monkeypatch.setattr(
            flash_overlay, "_push_bitmap", lambda *args: calls.setdefault("pushed", args)
        )
        monkeypatch.setattr(flash_overlay, "_pump_messages", lambda hwnd: None)
        monkeypatch.setattr(flash_overlay._user32, "DestroyWindow", lambda hwnd: None)
        monkeypatch.setattr(flash_overlay._user32, "UnregisterClassW", lambda *args: None)
        monkeypatch.setattr(flash_overlay.time, "perf_counter", iter([0.0, 1.0, 4.0]).__next__)

        overlay = flash_overlay._Overlay()
        flash_overlay._run_overlay([(-2560, 0, 0, 1440)], False, overlay)

        assert calls["window"][1:] == (-2560, 0, 2560, 1440)
        assert calls["glow"] == (2560, 1440, [(0, 0, 2560, 1440)], False)
        assert overlay.closed_event.is_set()

    def test_full_screen_capture_enumerates_monitors(self, monkeypatch):
        """When capture_rect is None the helper must read uia.GetMonitorsRect."""
        monkeypatch.delenv("WINDOWS_MCP_DISABLE_FLASH", raising=False)
        import windows_mcp.uia

        monkeypatch.setattr(
            windows_mcp.uia,
            "GetMonitorsRect",
            lambda: [_FakeRect(0, 0, 1920, 1080), _FakeRect(1920, 0, 3840, 1080)],
        )

        captured = {}

        class _StubThread:
            def __init__(self, target, args, name, daemon):
                captured["args"] = args

            def start(self):
                pass

        monkeypatch.setattr(flash_overlay.threading, "Thread", _StubThread)

        flash_overlay.show_capture_flash(None)

        rects_arg, full_screen_arg, _ = captured["args"]
        assert rects_arg == [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]
        assert full_screen_arg is True

    def test_overlapping_calls_cancel_prior_overlay(self, monkeypatch):
        """Second call must signal the prior overlay's stop_event so it can be torn down."""
        monkeypatch.delenv("WINDOWS_MCP_DISABLE_FLASH", raising=False)

        class _StubThread:
            def __init__(self, *a, **kw):
                pass

            def start(self):
                pass

        monkeypatch.setattr(flash_overlay.threading, "Thread", _StubThread)

        flash_overlay.show_capture_flash(_FakeRect(0, 0, 100, 100))
        first = flash_overlay._active_overlay
        assert first is not None
        assert not first.stop_event.is_set()

        flash_overlay.show_capture_flash(_FakeRect(0, 0, 100, 100))
        second = flash_overlay._active_overlay
        assert second is not None
        assert second is not first
        # Critical: the prior overlay must be signalled so cancel_active_flash semantics survive
        assert first.stop_event.is_set()


class TestCancelActiveFlash:
    def test_no_op_when_no_active_overlay(self):
        flash_overlay.cancel_active_flash()
        assert flash_overlay._active_overlay is None

    def test_signals_stop_and_clears_active(self, monkeypatch):
        # Install a stub overlay manually so we don't depend on Tk
        overlay = flash_overlay._Overlay()
        overlay.thread = threading.Thread(target=lambda: None, daemon=True)
        overlay.thread.start()
        overlay.thread.join()
        with flash_overlay._lock:
            flash_overlay._active_overlay = overlay

        flash_overlay.cancel_active_flash(timeout=0.1)

        assert overlay.stop_event.is_set()
        assert flash_overlay._active_overlay is None


class TestIntensityCurve:
    def test_full_screen_is_bell_curve(self):
        # Symmetric peak at t=0.5, zero at t=0 and t=1
        assert flash_overlay._intensity_at(0.0, full_screen=True) == 0.0
        assert flash_overlay._intensity_at(0.5, full_screen=True) == 1.0
        assert abs(flash_overlay._intensity_at(1.0, full_screen=True)) < 1e-9

    def test_region_holds_then_fades(self):
        # Pre-peak fade-in
        assert flash_overlay._intensity_at(0.0, full_screen=False) == 0.0
        # Held at peak in the middle
        assert flash_overlay._intensity_at(0.4, full_screen=False) == 1.0
        # Fading in last segment
        assert flash_overlay._intensity_at(1.0, full_screen=False) == 0.0


class TestPremultipliedBgra:
    def test_full_intensity_premultiplies_color_by_alpha(self):
        from PIL import Image

        # 1×1 pixel: orange-red 50% alpha → premult should be (R*128/255, G*128/255, B*128/255, 128) in BGRA order
        img = Image.new("RGBA", (1, 1), (255, 69, 0, 128))
        out = flash_overlay._premultiplied_bgra(img, 1.0)
        b, g, r, a = out
        assert a == 128
        assert b == 0
        assert g == (69 * 128) // 255
        assert r == (255 * 128) // 255

    def test_intensity_scales_alpha(self):
        from PIL import Image

        img = Image.new("RGBA", (1, 1), (255, 69, 0, 255))
        out = flash_overlay._premultiplied_bgra(img, 0.5)
        # Alpha was 255; intensity 0.5 → effective alpha ≈ 127.
        _, _, _, a = out
        assert 124 <= a <= 128


class TestRunOverlayFallthrough:
    def test_missing_pillow_sets_closed_event(self, monkeypatch):
        # Force ``from PIL import Image`` inside _run_overlay to fail before Win32 window creation.
        monkeypatch.setitem(sys.modules, "PIL", None)
        overlay = flash_overlay._Overlay()
        flash_overlay._run_overlay([(0, 0, 100, 100)], False, overlay)
        assert overlay.closed_event.is_set()
