"""Pure bitmap artwork shared by the active AI-control indicator."""

import math
import os
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from windows_mcp.desktop.overlay_bitmap import premultiplied_bgra

_BORDER = 56
_CURSOR_SIZE = 88
_NOTICE_GLOW_PAD = 32
_BREATH_PERIOD_SECONDS = 3.0
_BREATH_MIN_ALPHA = 140
_NOTICE_TITLE = "AI is controlling this computer"
_NOTICE_SHORTCUT = "Ctrl + Alt + Shift + Backspace"
_NOTICE_HINT = f"Press {_NOTICE_SHORTCUT} to take over"


def _breath_opacity(elapsed: float) -> int:
    """Start bright and cycle smoothly while keeping the control cue visible."""
    strength = (1 + math.cos(2 * math.pi * elapsed / _BREATH_PERIOD_SECONDS)) / 2
    return round(_BREATH_MIN_ALPHA + (255 - _BREATH_MIN_ALPHA) * strength)


def _glow_alpha(depth: int, extent: int = _BORDER) -> int:
    """Peak at the screen edge and fade continuously to transparency inward."""
    if depth >= extent - 1:
        return 0
    return round(240 * (1 - depth / (extent - 1)) ** 1.4)


def _edge_bitmap(width: int, height: int, side: str, color: tuple[int, int, int]) -> bytes:
    """Pre-render feathered edges, joining corners at one glow strength."""
    image = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(image)
    extent = min(_BORDER, height if side in ("top", "bottom") else width)
    corner_width = min(extent, width // 2)
    for depth in range(extent):
        # Let the gradient itself reach the edge; do not draw a separate outline.
        alpha = _glow_alpha(depth, extent)
        if side in ("top", "bottom"):
            y = depth if side == "top" else height - 1 - depth
            draw.line((0, y, width - 1, y), fill=(*color, alpha))
            # The side strips start below/above these corner squares. Use the
            # nearest screen edge as the depth, matching the straight strips
            # without blending two windows or drawing a diagonal bright seam.
            for side_depth in range(corner_width):
                corner_alpha = _glow_alpha(min(depth, side_depth), extent)
                draw.point((side_depth, y), fill=(*color, corner_alpha))
                draw.point((width - 1 - side_depth, y), fill=(*color, corner_alpha))
        elif side == "left":
            draw.line((depth, 0, depth, height - 1), fill=(*color, alpha))
        else:
            x = width - 1 - depth
            draw.line((x, 0, x, height - 1), fill=(*color, alpha))
    return premultiplied_bgra(image, 1.0)


def _cursor_bitmap(color: tuple[int, int, int]) -> bytes:
    """Build a broad cursor aura with no drawn ellipse or sharp contour."""
    half = (_CURSOR_SIZE - 1) / 2
    pixels = []
    for y in range(_CURSOR_SIZE):
        for x in range(_CURSOR_SIZE):
            radius = math.hypot(x - half, y - half)
            clear_center = 1 - math.exp(-0.5 * (max(0.0, radius - 1) / 5) ** 2)
            outer_glow = math.exp(-0.5 * (radius / 20) ** 2)
            edge_fade = min(1.0, max(0.0, (half - radius) / 8))
            alpha = round(230 * clear_center * outer_glow * edge_fade)
            pixels.append((*color, alpha))
    image = Image.new("RGBA", (_CURSOR_SIZE, _CURSOR_SIZE))
    image.putdata(pixels)
    return premultiplied_bgra(image, 1.0)


def _notice_shape_mask(width: int, height: int, radius: int = 22) -> Image.Image:
    """Downsample a rounded mask so all four corners have smooth pixel coverage."""
    scale = 8
    mask = Image.new("L", (width * scale, height * scale))
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1), radius=radius * scale, fill=255
    )
    return mask.resize((width, height), Image.Resampling.LANCZOS).filter(
        ImageFilter.GaussianBlur(3)
    )


def _notice_panel_mask(width: int, height: int) -> Image.Image:
    """Fade the rounded panel through its outer eight pixels."""
    edge_fade = [round(255 * (step / 8) ** 2 * (3 - 2 * step / 8)) for step in range(9)]
    horizontal = [edge_fade[min(8, x, width - 1 - x)] for x in range(width)]
    vertical = [edge_fade[min(8, y, height - 1 - y)] for y in range(height)]
    fade_mask = Image.new("L", (width, height))
    fade_mask.putdata(
        [min(horizontal[x], vertical[y]) for y in range(height) for x in range(width)]
    )
    return ImageChops.multiply(_notice_shape_mask(width, height), fade_mask)


def _notice_bitmap(screen_width: int) -> tuple[int, int, bytes] | None:
    """Render a centered two-line prompt below the upper glow."""
    available_width = screen_width - 32
    if available_width < 200:
        return None
    horizontal_padding = 32 if screen_width >= 640 else 20
    font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    # Prefer presentation-sized text, then shrink only when a monitor is narrow.
    for hint_size in range(24, 7, -1):
        try:
            title_font = ImageFont.truetype(str(font_dir / "seguisb.ttf"), hint_size + 8)
            hint_font = ImageFont.truetype(str(font_dir / "segoeui.ttf"), hint_size)
        except OSError:
            title_font = hint_font = ImageFont.load_default()
        measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        title_box = measure.textbbox((0, 0), _NOTICE_TITLE, font=title_font)
        hint_box = measure.textbbox((0, 0), _NOTICE_HINT, font=hint_font)
        hint_prefix, _, hint_suffix = _NOTICE_HINT.partition(_NOTICE_SHORTCUT)
        prefix_width = math.ceil(measure.textlength(hint_prefix, font=hint_font))
        shortcut_width = math.ceil(measure.textlength(_NOTICE_SHORTCUT, font=hint_font))
        suffix_width = math.ceil(measure.textlength(hint_suffix, font=hint_font))
        title_width, title_height = title_box[2] - title_box[0], title_box[3] - title_box[1]
        hint_width, hint_height = (
            prefix_width + shortcut_width + suffix_width + 16,
            hint_box[3] - hint_box[1],
        )
        width = max(title_width, hint_width) + 2 * horizontal_padding
        if width <= available_width:
            break
    else:
        return None

    height = title_height + hint_height + 46
    image = Image.new("RGBA", (width, height), (8, 42, 88, 0))
    # Fade every edge from transparent to 80%; the rounded mask softens the corners too.
    image.putalpha(_notice_panel_mask(width, height).point(lambda alpha: round(alpha * 0.8)))
    draw = ImageDraw.Draw(image)
    title_y = 18 - title_box[1]
    hint_y = 28 + title_height - hint_box[1]
    draw.text(
        ((width - title_width) // 2 - title_box[0], title_y),
        _NOTICE_TITLE,
        font=title_font,
        fill=(248, 251, 255, 255),
    )
    hint_x = (width - hint_width) // 2
    badge_left = hint_x + prefix_width
    # A solid black key label separates the takeover shortcut from its sentence.
    draw.rounded_rectangle(
        (
            badge_left,
            24 + title_height,
            badge_left + shortcut_width + 15,
            31 + title_height + hint_height,
        ),
        radius=6,
        fill=(0, 0, 0, 255),
    )
    draw.text((hint_x, hint_y), hint_prefix, font=hint_font, fill=(225, 240, 255, 255))
    draw.text((badge_left + 8, hint_y), _NOTICE_SHORTCUT, font=hint_font, fill=(255, 255, 255, 255))
    draw.text(
        (badge_left + shortcut_width + 16, hint_y),
        hint_suffix,
        font=hint_font,
        fill=(225, 240, 255, 255),
    )
    return width, height, premultiplied_bgra(image, 1.0)


def _notice_glow_bitmap(
    width: int, height: int, color: tuple[int, int, int], pad: int = _NOTICE_GLOW_PAD
) -> bytes:
    """Anchor the aura at the solid start of the panel's outer fade."""
    size = (width + 2 * pad, height + 2 * pad)
    shape = Image.new("L", size)
    # The inset contour meets the panel where its eight-pixel fade begins.
    shape.paste(_notice_shape_mask(width - 16, height - 16, 14), (pad + 8, pad + 8))
    outside = ImageChops.subtract(shape.filter(ImageFilter.GaussianBlur(min(15, pad / 2))), shape)
    # Feather the inner cutoff as well, so the halo cannot end as a bright line.
    outside = outside.filter(ImageFilter.GaussianBlur(3))
    aura = Image.new("RGBA", size, (*color, 0))
    peak = outside.getextrema()[1]
    aura.putalpha(outside.point(lambda alpha: round(alpha * 211 / peak)))
    return premultiplied_bgra(aura, 1.0)
