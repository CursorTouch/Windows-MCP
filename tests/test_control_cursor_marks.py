"""AI label on the cursor glow and the ripple that marks each AI click."""

import time

import pytest

from windows_mcp.desktop import control_overlay, control_overlay_art, flash_overlay, service
from windows_mcp.desktop.service import Desktop


def _alpha(bgra: bytes, width: int, x: int, y: int) -> int:
    return bgra[(y * width + x) * 4 + 3]


def test_cursor_label_sits_above_right_of_the_hotspot():
    size = control_overlay_art._CURSOR_SIZE
    glow = control_overlay_art._cursor_bitmap((45, 145, 255))
    half = size // 2
    assert _alpha(glow, size, half + 14, half - 12) == 255  # Opaque label.
    assert _alpha(glow, size, half - 14, half + 14) < 255  # Arrow side keeps the soft glow.


def test_ripple_grows_and_fades():
    size = control_overlay_art._RIPPLE_SIZE
    frames = control_overlay_art._ripple_frames((45, 145, 255))
    assert len(frames) == control_overlay_art._RIPPLE_FRAMES
    assert all(len(frame) == size * size * 4 for frame in frames)
    peaks = [max(frame[3::4]) for frame in frames]
    assert peaks == sorted(peaks, reverse=True) and peaks[-1] == 0
    row = [_alpha(frames[0], size, x, size // 2) for x in range(size)]
    later = [_alpha(frames[4], size, x, size // 2) for x in range(size)]
    assert row.index(max(row)) > later.index(max(later))  # The ring moved outward.


def test_ripple_animates_only_while_ai_control_shows(monkeypatch):
    draws, shown = [], []

    class FakeLayer:
        hwnd = 1
        breathes = True

        def __init__(self, *args, **kwargs):
            pass

        def set_opacity(self, opacity):
            pass

        def show(self):
            shown.append(True)

        def hide(self):
            shown.append(False)

        def move(self, x, y):
            pass

        def draw(self, bgra, x, y):
            draws.append((bgra, x, y))

        def close(self):
            pass

    monkeypatch.setattr(control_overlay, "_monitor_rects", lambda: ((0, 0, 800, 600),))
    monkeypatch.setattr(
        control_overlay, "_build_layers", lambda *args, **kwargs: ([FakeLayer()], FakeLayer())
    )
    monkeypatch.setattr(control_overlay, "_Layer", FakeLayer)
    monkeypatch.setattr(control_overlay, "_ripple_frames", lambda color: list(range(9)))
    monkeypatch.setattr(control_overlay._user32, "GetCursorPos", lambda ptr: True)
    monkeypatch.setattr(flash_overlay, "_pump_messages", lambda hwnd: None)
    control_overlay.start()
    try:
        control_overlay.ripple(100, 200)  # Not under AI control: dropped.
        time.sleep(0.2)
        assert draws == []
        control_overlay.set_active(True)
        shown.clear()
        control_overlay.ripple(100, 200)
        deadline = time.monotonic() + 3
        while shown[-1:] != [False] and time.monotonic() < deadline:
            time.sleep(0.05)
        half = control_overlay_art._RIPPLE_SIZE // 2
        assert {(x, y) for _, x, y in draws} == {(100 - half, 200 - half)}
        assert [frame for frame, _, _ in draws] == sorted(frame for frame, _, _ in draws)
        assert shown[-1] is False
    finally:
        control_overlay.stop()


@pytest.mark.parametrize(("clicks", "ripples"), [(1, [(30, 40)]), (0, [])])
def test_click_marks_the_point_but_hover_does_not(monkeypatch, clicks, ripples):
    marked = []
    desktop = Desktop.__new__(Desktop)
    monkeypatch.setattr(service.uia, "GetVirtualScreenRect", lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(service.uia, "Click", lambda x, y: None)
    monkeypatch.setattr(service.uia, "SetCursorPos", lambda x, y: None)
    monkeypatch.setattr(control_overlay, "ripple", lambda x, y: marked.append((x, y)))
    desktop.click(loc=[30, 40], clicks=clicks)
    assert marked == ripples
