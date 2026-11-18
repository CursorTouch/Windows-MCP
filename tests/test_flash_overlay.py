"""Unit tests for the screenshot flash overlay.

The actual Tk window is never created in these tests — they exercise the
public dispatch surface (env-var gating, lifecycle bookkeeping, fallthrough
when ``tkinter`` cannot be imported).
"""

import itertools
import sys
import threading
import ctypes
from ctypes import wintypes
from unittest.mock import patch

import pytest

import windows_mcp.desktop.flash_overlay as flash_overlay


@pytest.fixture(autouse=True)
def _reset_active_overlay():
    """Each test starts and ends with no overlay registered."""
    with flash_overlay._lock:
        flash_overlay._active_overlay = None
        flash_overlay._all_overlays.clear()
    yield
    with flash_overlay._lock:
        ov = flash_overlay._active_overlay
        flash_overlay._active_overlay = None
        all_overlays = tuple(flash_overlay._all_overlays)
        flash_overlay._all_overlays.clear()
    if ov is not None:
        ov.stop_event.set()
    for pending in all_overlays:
        pending.stop_event.set()


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


def test_window_api_signatures_preserve_x64_handles():
    """Without argtypes ctypes narrows Python integers to 32-bit c_int."""
    assert ctypes.sizeof(flash_overlay._kernel32.GetModuleHandleW.restype) == ctypes.sizeof(
        ctypes.c_void_p
    )
    assert flash_overlay._user32.CreateWindowExW.argtypes[10] is wintypes.HINSTANCE
    assert flash_overlay._user32.UnregisterClassW.argtypes[1] is wintypes.HINSTANCE


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

    def test_rejects_capture_when_replaced_flash_has_not_closed(self):
        old = flash_overlay._Overlay()
        latest = flash_overlay._Overlay()
        latest.closed_event.set()
        with flash_overlay._lock:
            flash_overlay._all_overlays.update((old, latest))
            flash_overlay._active_overlay = latest

        assert flash_overlay.cancel_active_flash(timeout=0) is False
        assert old.stop_event.is_set()
        assert latest.stop_event.is_set()
        assert flash_overlay._active_overlay is None

        old.closed_event.set()
        assert flash_overlay.cancel_active_flash(timeout=0) is True


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


def _fake_win32(
    monkeypatch,
    *,
    clock_step: float = 0.03,
    frame_times: "list[float] | None" = None,
    stop_after: int | None = None,
):
    """Replace the Win32 calls of ``_run_overlay`` with recorders.

    The fake clock advances ``clock_step`` seconds per read (one read per
    frame), and the per-frame wait on the stop event returns at once instead
    of sleeping, so a whole flash runs instantly with the same frame cadence.
    ``frame_times`` instead advances the clock by each of those intervals in
    turn, repeating, to stand in for a real timer's jittered frame waits.
    ``stop_after`` sets the stop event after that many opacity changes,
    standing in for a newer capture cancelling the flash.
    """
    calls = {"windows": [], "uploads": [], "opacity": [], "destroyed": [], "unregistered": []}
    overlay_ref = {}

    def create(class_name, x, y, width, height):
        hwnd = 100 + len(calls["windows"])
        calls["windows"].append((hwnd, class_name, x, y, width, height))
        return hwnd, 7

    def upload(hwnd, x, y, width, height, bgra, *, opacity=255):
        assert len(bgra) == width * height * 4
        calls["uploads"].append((hwnd, x, y, width, height, opacity))

    def set_opacity(hwnd, opacity):
        calls["opacity"].append((hwnd, opacity))
        if stop_after is not None and len(calls["opacity"]) >= stop_after:
            overlay_ref["overlay"].stop_event.set()

    steps = itertools.cycle(frame_times) if frame_times else itertools.repeat(clock_step)
    clock = itertools.accumulate(steps, initial=0.0)
    monkeypatch.setattr(flash_overlay, "_create_layered_window", create)
    monkeypatch.setattr(flash_overlay, "_push_bitmap", upload)
    monkeypatch.setattr(flash_overlay, "_set_window_opacity", set_opacity, raising=False)
    monkeypatch.setattr(flash_overlay, "_pump_messages", lambda hwnd: None)
    monkeypatch.setattr(flash_overlay._user32, "ShowWindow", lambda *args: None)
    monkeypatch.setattr(flash_overlay._user32, "SetWindowPos", lambda *args: None)
    monkeypatch.setattr(
        flash_overlay._user32, "DestroyWindow", lambda hwnd: calls["destroyed"].append(hwnd)
    )
    monkeypatch.setattr(
        flash_overlay._user32,
        "UnregisterClassW",
        lambda name, instance: calls["unregistered"].append(name),
    )
    monkeypatch.setattr(flash_overlay.time, "perf_counter", lambda: next(clock))

    def run(rects, full_screen):
        overlay = flash_overlay._Overlay()
        overlay_ref["overlay"] = overlay
        stop_event = overlay.stop_event
        # The frame's wait would really sleep; the fake clock already moved.
        stop_event.wait = lambda timeout=None: stop_event.is_set()
        flash_overlay._run_overlay(rects, full_screen, overlay)
        return overlay

    return calls, run


@pytest.fixture
def fresh_glow_cache():
    flash_overlay._glow_strips.cache_clear()
    yield
    flash_overlay._glow_strips.cache_clear()


def _union(rects):
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )


def _compose(strips, union):
    """Paste strip pixels into one union-sized premultiplied BGRA buffer."""
    left, top, right, bottom = union
    width = right - left
    canvas = bytearray(width * (bottom - top) * 4)
    for strip in strips:
        row = strip.width * 4
        for y in range(strip.height):
            offset = ((strip.y - top + y) * width + (strip.x - left)) * 4
            assert canvas[offset : offset + row] == bytes(row), "strips overlap"
            canvas[offset : offset + row] = strip.bgra[y * row : (y + 1) * row]
    return bytes(canvas)


def _on_screen(buffer, rects, union):
    """Zero every pixel that no monitor shows (the gaps of a ragged layout)."""
    width = union[2] - union[0]
    kept = bytearray(len(buffer))
    for left, top, right, bottom in rects:
        for y in range(top - union[1], bottom - union[1]):
            start = (y * width + left - union[0]) * 4
            end = start + (right - left) * 4
            kept[start:end] = buffer[start:end]
    return bytes(kept)


@pytest.mark.usefixtures("fresh_glow_cache")
class TestGlowStrips:
    @pytest.mark.parametrize(
        ("rects", "full_screen"),
        [
            # Region capture whose glow touches the window edge.
            (((-600, 30, -100, 330),), False),
            # Monitors of different heights with negative coordinates, like a
            # 3440x1440 + offset 1920x1200 desktop: corners, the shared edge and
            # the gap under the shorter monitor must all match.
            (((-400, 95, 0, 335), (0, 0, 520, 360)), True),
            # A display smaller than two strip thicknesses.
            (((0, 0, 90, 70),), True),
        ],
    )
    def test_strips_reproduce_the_single_full_union_glow(self, rects, full_screen):
        """Edge strips together equal the former one-window bitmap, pixel for pixel."""
        union = _union(rects)
        inset = flash_overlay._FULLSCREEN_INSET if full_screen else 0
        local = [
            (
                left - union[0] + inset,
                top - union[1] + inset,
                right - union[0] - inset,
                bottom - union[1] - inset,
            )
            for left, top, right, bottom in rects
        ]
        reference = flash_overlay._premultiplied_bgra(
            flash_overlay._render_glow_rgba(
                union[2] - union[0], union[3] - union[1], local, outward=False
            ),
            1.0,
        )
        composed = _compose(flash_overlay._glow_strips(rects, full_screen), union)
        assert _on_screen(composed, rects, union) == _on_screen(reference, rects, union)

    def test_strip_pixels_scale_with_perimeter_not_area(self):
        width, height = 3840, 2160
        strips = flash_overlay._glow_strips(((0, 0, width, height),), True)
        pixels = sum(s.width * s.height for s in strips)
        assert len(strips) == 4
        assert pixels <= 2 * (width + height) * 64
        assert pixels < width * height // 10


def _opacity_steps(calls, hwnd):
    """A strip's opacity history, starting from its hidden (opacity 0) upload."""
    return [0] + [opacity for h, opacity in calls["opacity"] if h == hwnd]


class TestRunOverlay:
    @pytest.mark.parametrize("full_screen", [True, False])
    def test_each_strip_is_uploaded_once_and_fades_smoothly_in_then_out(
        self, monkeypatch, fresh_glow_cache, full_screen
    ):
        calls, run = _fake_win32(monkeypatch)
        overlay = run([(0, 0, 1440, 900)], full_screen)

        hwnds = [w[0] for w in calls["windows"]]
        assert len(hwnds) == 4
        # Pixels go up exactly once per strip, hidden; the fade only swaps alpha.
        assert sorted(u[0] for u in calls["uploads"]) == hwnds
        assert all(u[5] == 0 for u in calls["uploads"])
        for hwnd in hwnds:
            steps = _opacity_steps(calls, hwnd)
            peak = steps.index(max(steps))
            assert steps[peak] == 255
            assert steps[: peak + 1] == sorted(steps[: peak + 1])  # fade in, no reversal
            assert steps[peak:] == sorted(steps[peak:], reverse=True)  # fade out, no reversal
            assert steps[-1] == 0
            # The fake clock advances one 30 ms frame per read, so no frame may
            # skip a level: every change is at most one level (255 / 16 ~ 16).
            assert max(abs(b - a) for a, b in zip(steps, steps[1:])) <= 16
        assert sorted(calls["destroyed"]) == hwnds
        assert len(calls["unregistered"]) == 4
        assert overlay.closed_event.is_set()

    @pytest.mark.parametrize("full_screen", [True, False])
    def test_jittered_frames_never_skip_an_opacity_level(
        self, monkeypatch, fresh_glow_cache, full_screen
    ):
        # Real Windows timer waits for a 30 ms frame land between 31 and 47 ms,
        # often several 47 ms frames in a row. The region fade-in is only
        # ~525 ms (~33 ms per level), so late frames must not jump levels.
        jitter = [0.047, 0.047, 0.047, 0.031, 0.039, 0.047, 0.035, 0.047, 0.047, 0.043]
        calls, run = _fake_win32(monkeypatch, frame_times=jitter)
        run([(0, 0, 1440, 900)], full_screen)

        for hwnd in [w[0] for w in calls["windows"]]:
            steps = _opacity_steps(calls, hwnd)
            peak = steps.index(max(steps))
            assert steps[peak] == 255
            assert steps[: peak + 1] == sorted(steps[: peak + 1])
            assert steps[peak:] == sorted(steps[peak:], reverse=True)
            assert steps[-1] == 0
            assert max(abs(b - a) for a, b in zip(steps, steps[1:])) <= 16

    @pytest.mark.parametrize("full_screen", [True, False])
    def test_the_flash_lasts_its_full_duration(self, monkeypatch, fresh_glow_cache, full_screen):
        frames = []
        calls, run = _fake_win32(monkeypatch, clock_step=flash_overlay._FRAME_INTERVAL_MS / 1000)
        monkeypatch.setattr(flash_overlay, "_pump_messages", lambda hwnd: frames.append(hwnd))
        run([(0, 0, 1440, 900)], full_screen)

        expected = flash_overlay._DURATION_MS / flash_overlay._FRAME_INTERVAL_MS
        assert abs(frames.count(calls["windows"][0][0]) - expected) <= 2

    @pytest.mark.parametrize("full_screen", [True, False])
    def test_a_finished_flash_is_fully_transparent_before_its_windows_close(
        self, monkeypatch, fresh_glow_cache, full_screen
    ):
        # A slow 250 ms frame (a busy machine) puts the last frame well before
        # the curve reaches zero, so the fade itself stops at a visible level.
        calls, run = _fake_win32(monkeypatch, clock_step=0.25)
        run([(0, 0, 1440, 900)], full_screen)

        for hwnd in [w[0] for w in calls["windows"]]:
            steps = [opacity for h, opacity in calls["opacity"] if h == hwnd]
            assert steps[-1] == 0

    def test_region_flash_windows_stay_inside_the_capture_rect(self, monkeypatch, fresh_glow_cache):
        calls, run = _fake_win32(monkeypatch)
        run([(-2560, 0, 0, 1440)], False)

        for _, _, x, y, width, height in calls["windows"]:
            assert -2560 <= x and x + width <= 0
            assert 0 <= y and y + height <= 1440
        covered = sum(w[4] * w[5] for w in calls["windows"])
        assert covered <= 2 * (2560 + 1440) * 64

    def test_repeated_flashes_reuse_the_glow_until_the_layout_changes(
        self, monkeypatch, fresh_glow_cache
    ):
        renders = []
        real_render = flash_overlay._render_glow_rgba

        def counting_render(*args, **kwargs):
            renders.append(args[:2])
            return real_render(*args, **kwargs)

        monkeypatch.setattr(flash_overlay, "_render_glow_rgba", counting_render)
        _, run = _fake_win32(monkeypatch)
        run([(0, 0, 800, 600)], True)
        first = len(renders)
        assert first > 0
        run([(0, 0, 800, 600)], True)
        assert len(renders) == first
        run([(0, 0, 1024, 768)], True)
        assert len(renders) > first

    def test_cancel_stops_the_fade_and_closes_every_window(self, monkeypatch, fresh_glow_cache):
        calls, run = _fake_win32(monkeypatch, stop_after=2)
        overlay = run([(0, 0, 640, 480), (640, 0, 1280, 480)], True)

        assert len(calls["windows"]) == 8
        assert len(calls["opacity"]) <= 8
        assert sorted(calls["destroyed"]) == [w[0] for w in calls["windows"]]
        assert overlay.closed_event.is_set()

    def test_failed_window_creation_releases_the_windows_already_made(
        self, monkeypatch, fresh_glow_cache
    ):
        calls, run = _fake_win32(monkeypatch)
        recording_create = flash_overlay._create_layered_window

        def fail_third(*args):
            if len(calls["windows"]) == 2:
                raise OSError("CreateWindowExW failed")
            return recording_create(*args)

        monkeypatch.setattr(flash_overlay, "_create_layered_window", fail_third)
        overlay = run([(0, 0, 640, 480)], True)

        assert sorted(calls["destroyed"]) == [100, 101]
        assert len(calls["unregistered"]) == 2
        assert overlay.closed_event.is_set()
