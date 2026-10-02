"""Regression checks for the accepted AI-control glow and notice."""

import pytest

from windows_mcp.desktop import control_overlay, control_overlay_art, flash_overlay


def _assert_premultiplied(bgra: bytes) -> None:
    for offset in range(0, len(bgra), 4):
        blue, green, red, alpha = bgra[offset : offset + 4]
        assert blue <= alpha and green <= alpha and red <= alpha


def _alpha_at(bgra: bytes, width: int, x: int, y: int) -> int:
    return bgra[(y * width + x) * 4 + 3]


def test_notice_uses_clear_english_and_transparent_corners():
    assert control_overlay_art._NOTICE_TITLE == "AI is controlling this computer"
    assert control_overlay_art._NOTICE_HINT == "Press Ctrl + Alt + Shift + Backspace to take over"
    assert control_overlay_art._NOTICE_SHORTCUT == "Ctrl + Alt + Shift + Backspace"
    notice = control_overlay_art._notice_bitmap(1920)
    assert notice is not None
    width, height, bgra = notice
    assert 540 <= width < 1920 and height < 120  # No third line remains.
    assert len(bgra) == width * height * 4
    # A supersampled rounded mask gives each corner several partial-alpha pixels.
    for x_start in (0, width - 22):
        for y_start in (0, height - 22):
            corner_alpha = {
                _alpha_at(bgra, width, x_start + dx, y_start + dy)
                for dx in range(22)
                for dy in range(22)
            }
            assert len({alpha for alpha in corner_alpha if 0 < alpha < 204}) >= 8
    for x in (0, width - 1):
        for y in (0, height - 1):
            assert _alpha_at(bgra, width, x, y) == 0
    # Each straight side now tapers from transparent to the 80%-opaque center.
    for edge in (
        [_alpha_at(bgra, width, d, height // 2) for d in range(9)],
        [_alpha_at(bgra, width, width - 1 - d, height // 2) for d in range(9)],
        [_alpha_at(bgra, width, width // 2, d) for d in range(9)],
        [_alpha_at(bgra, width, width // 2, height - 1 - d) for d in range(9)],
    ):
        assert edge[0] == 0 < edge[1] < edge[4] < edge[8] == 204
        assert all(a <= b for a, b in zip(edge, edge[1:]))
    assert _alpha_at(bgra, width, 10, height // 2) == 204  # 204/255 is 0.8.
    assert max(bgra[3::4]) == 255  # Text stays fully legible while the glow breathes.
    assert any(
        bgra[offset : offset + 4] == b"\x00\x00\x00\xff" for offset in range(0, len(bgra), 4)
    )
    narrow = control_overlay_art._notice_bitmap(300)
    assert narrow is not None and narrow[0] <= 268


def test_notice_outer_glow_fades_without_changing_the_panel():
    width, height = 400, 100
    pad = control_overlay_art._NOTICE_GLOW_PAD
    glow = control_overlay_art._notice_glow_bitmap(width, height, (45, 145, 255))
    glow_width = width + 2 * pad
    _assert_premultiplied(glow)
    assert len(glow) == glow_width * (height + 2 * pad) * 4
    middle_y = pad + height // 2
    assert _alpha_at(glow, glow_width, pad + width // 2, middle_y) == 0
    assert _alpha_at(glow, glow_width, 0, 0) == 0
    assert (
        208 <= max(glow[3::4]) <= 214
    )  # Keep the accepted aura brightness after shifting it inward.
    assert (
        _alpha_at(glow, glow_width, pad - 1, middle_y)
        > _alpha_at(glow, glow_width, pad - 15, middle_y)
        > 0
    )


def test_notice_glow_meets_the_feathered_background():
    notice = control_overlay_art._notice_bitmap(1920)
    assert notice is not None
    width, height, panel = notice
    pad = control_overlay_art._NOTICE_GLOW_PAD
    halo = control_overlay_art._notice_glow_bitmap(width, height, (45, 145, 255))
    halo_width = width + 2 * pad
    for edge in (
        [(depth, height // 2) for depth in range(13)],
        [(width - 1 - depth, height // 2) for depth in range(13)],
        [(width // 2, depth) for depth in range(13)],
        [(width // 2, height - 1 - depth) for depth in range(13)],
    ):
        combined = []
        for depth, (panel_x, panel_y) in enumerate(edge):
            panel_alpha = _alpha_at(panel, width, panel_x, panel_y)
            halo_alpha = _alpha_at(halo, halo_width, pad + panel_x, pad + panel_y)
            if depth < 8:
                assert halo_alpha > 0
            if depth == 8:
                assert panel_alpha == 204
                assert halo_alpha > 0  # The aura now passes the fade's start.
            combined.append(round(panel_alpha + halo_alpha * (255 - panel_alpha) / 255))
        assert max(abs(a - b) for a, b in zip(combined, combined[1:])) <= 15
    for x_edge, y_edge, x_step, y_step in (
        (0, 0, 1, 1),
        (width - 1, 0, -1, 1),
        (0, height - 1, 1, -1),
        (width - 1, height - 1, -1, -1),
    ):
        # The rounded silhouette also needs a gradual combined transition.
        combined = []
        for depth in range(16):
            x, y = x_edge + depth * x_step, y_edge + depth * y_step
            panel_alpha = _alpha_at(panel, width, x, y)
            halo_alpha = _alpha_at(halo, halo_width, pad + x, pad + y)
            combined.append(round(panel_alpha + halo_alpha * (255 - panel_alpha) / 255))
        assert max(abs(a - b) for a, b in zip(combined, combined[1:])) <= 30


def test_breath_opacity_is_smooth_periodic_and_never_disappears():
    opacity = control_overlay_art._breath_opacity
    period = control_overlay_art._BREATH_PERIOD_SECONDS
    assert opacity(0) == opacity(period) == 255
    assert opacity(period / 2) == control_overlay_art._BREATH_MIN_ALPHA == 140
    assert opacity(period / 4) == opacity(3 * period / 4)
    frames = [opacity(index * control_overlay._REFRESH_SECONDS) for index in range(61)]
    assert all(140 <= value <= 255 for value in frames)
    assert max(abs(a - b) for a, b in zip(frames, frames[1:])) <= 7


def test_layer_opacity_updates_only_on_change_and_fails_closed(monkeypatch):
    layer = object.__new__(control_overlay._Layer)
    layer.hwnd = 123
    layer.x, layer.y, layer.width, layer.height = 10, 20, 1, 1
    layer.bgra = bytes((0, 0, 0, 0))
    layer.opacity = None
    calls = []

    def push_bitmap(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(flash_overlay, "_push_bitmap", push_bitmap)
    layer.set_opacity(200)
    layer.set_opacity(200)
    layer.set_opacity(140)
    assert calls == [
        ((123, 10, 20, 1, 1, layer.bgra), {"opacity": 200}),
        ((123, 10, 20, 1, 1, layer.bgra), {"opacity": 140}),
    ]

    def failed_upload(*args, **kwargs):
        raise OSError("upload failed")

    monkeypatch.setattr(flash_overlay, "_push_bitmap", failed_upload)
    with pytest.raises(OSError, match="upload failed"):
        layer.set_opacity(255)
    assert layer.opacity == 140


def test_edge_and_cursor_have_soft_glow_without_a_solid_contour():
    size = control_overlay_art._BORDER
    assert size == 56  # The requested doubling of the former 28-pixel glow.
    for side in ("left", "right", "top", "bottom"):
        width = size * 3 if side in ("top", "bottom") else size
        height = size if side in ("top", "bottom") else size * 3
        edge = control_overlay_art._edge_bitmap(width, height, side, (45, 145, 255))
        assert len(edge) == width * height * 4
        _assert_premultiplied(edge)
        alphas = (
            [_alpha_at(edge, width, x, height // 2) for x in range(width)]
            if side in ("left", "right")
            else [_alpha_at(edge, width, width // 2, y) for y in range(height)]
        )
        if side in ("right", "bottom"):
            alphas.reverse()
        assert alphas[0] == max(alphas) == 240  # Twice the previous edge opacity.
        assert alphas[-1] == 0
        assert all(a >= b for a, b in zip(alphas, alphas[1:]))
        assert max(a - b for a, b in zip(alphas, alphas[1:])) <= 8
        assert 30 <= alphas[40] <= 50  # The glow remains broad but fades inward.

    glow = control_overlay_art._cursor_bitmap((45, 145, 255))
    width = control_overlay_art._CURSOR_SIZE
    assert len(glow) == width**2 * 4
    _assert_premultiplied(glow)
    alphas = [glow[((width // 2) * width + x) * 4 + 3] for x in range(width // 2, width)]
    assert glow[3] == 0  # Transparent square corners.
    assert alphas[0] == 0 < alphas[10]  # Cursor center is fully transparent.
    assert alphas[10] > alphas[25] > alphas[-1]
    assert alphas[-1] == 0
    assert max(alphas) == 176  # Twice the previous cursor glow peak of 88.
    assert max(abs(a - b) for a, b in zip(alphas, alphas[1:])) < 30


def test_corner_glow_joins_edges_without_double_opacity():
    border = control_overlay_art._BORDER
    width = border * 4
    top = control_overlay_art._edge_bitmap(width, border, "top", (45, 145, 255))
    bottom = control_overlay_art._edge_bitmap(width, border, "bottom", (45, 145, 255))
    left = control_overlay_art._edge_bitmap(border, border, "left", (45, 145, 255))
    assert _alpha_at(top, width, 0, 0) == control_overlay_art._glow_alpha(0)
    assert _alpha_at(top, width, width - 1, 0) == control_overlay_art._glow_alpha(0)
    for horizontal in (top, bottom):
        for x_depth in (1, 5, 10, 20, 40, 55):
            side_alpha = _alpha_at(left, border, x_depth, border // 2)
            for y_depth in (1, 5, 10, 20, 40, 55):
                y = y_depth if horizontal is top else border - 1 - y_depth
                straight_alpha = _alpha_at(horizontal, width, width // 2, y)
                expected = control_overlay_art._glow_alpha(min(x_depth, y_depth))
                assert expected <= max(side_alpha, straight_alpha)
                assert _alpha_at(horizontal, width, x_depth, y) == expected
                assert _alpha_at(horizontal, width, width - 1 - x_depth, y) == expected


def test_multimonitor_creates_narrow_clickthrough_layers(monkeypatch):
    made = []

    class FakeLayer:
        def __init__(self, x, y, width, height, bgra, name, *, breathes=True):
            made.append((x, y, width, height, name, breathes))

        def close(self):
            pass

    monkeypatch.setattr(control_overlay, "_Layer", FakeLayer)
    monkeypatch.setattr(control_overlay._user32, "GetCursorPos", lambda ptr: True)
    rects = ((-1920, 0, 0, 1080), (0, 0, 2560, 1440))
    layers, ring = control_overlay._build_layers(rects, pending=False)
    assert len(layers) == 12
    assert ring is not None
    border = control_overlay_art._BORDER
    assert made[0][:4] == (-1920, 0, 1920, border)
    assert made[2][:4] == (-1920, border, border, 1080 - 2 * border)
    assert made[3][:4] == (-border, border, border, 1080 - 2 * border)
    assert made[4][1] == border and made[4][4:] == ("0_notice_glow", True)
    assert made[5][1] == border + control_overlay_art._NOTICE_GLOW_PAD
    assert made[5][4:] == ("0_notice", False)
    assert made[4][0] == made[5][0] - control_overlay_art._NOTICE_GLOW_PAD
    assert abs((made[5][0] + made[5][2] / 2) - (-1920 / 2)) <= 1
    assert made[6][:4] == (0, 0, 2560, border)
    assert made[8][:4] == (0, border, border, 1440 - 2 * border)
    assert made[9][:4] == (2560 - border, border, border, 1440 - 2 * border)
    assert made[10][1] == border and made[10][4:] == ("1_notice_glow", True)
    assert made[11][1] == border + control_overlay_art._NOTICE_GLOW_PAD
    assert made[11][4:] == ("1_notice", False)
    assert abs((made[11][0] + made[11][2] / 2) - 1280) <= 1
    assert all(
        w <= border or h <= border
        for _, _, w, h, name, _ in made
        if name != "cursor" and not name.endswith(("notice", "notice_glow"))
    )


@pytest.mark.parametrize("width", [300, 400])
def test_notice_glow_stays_on_its_monitor_when_narrow(monkeypatch, width):
    made = []

    class FakeLayer:
        def __init__(self, x, y, layer_width, height, bgra, name, *, breathes=True):
            made.append((x, y, layer_width, height, name))

        def close(self):
            pass

    monkeypatch.setattr(control_overlay, "_Layer", FakeLayer)
    monkeypatch.setattr(control_overlay._user32, "GetCursorPos", lambda ptr: True)
    control_overlay._build_layers(((100, 0, 100 + width, 300),), pending=False)
    glow = next(layer for layer in made if layer[4] == "0_notice_glow")
    assert 100 <= glow[0]
    assert glow[0] + glow[2] <= 100 + width


def test_layer_failure_closes_prior_windows(monkeypatch):
    closed = []

    class FailingLayer:
        count = 0

        def __init__(self, *args):
            self.count = FailingLayer.count
            FailingLayer.count += 1
            if self.count == 2:
                raise OSError("window creation failed")

        def close(self):
            closed.append(self.count)

    monkeypatch.setattr(control_overlay, "_Layer", FailingLayer)
    with pytest.raises(OSError, match="window creation failed"):
        control_overlay._build_layers(((0, 0, 800, 600),), pending=False)
    assert closed == [1, 0]
