"""premultiplied_bgra must match the per-pixel arithmetic it replaced, byte for byte."""

import os

import pytest
from PIL import Image

from windows_mcp.desktop.overlay_bitmap import premultiplied_bgra


def _reference(rgba_image: Image.Image, intensity: float) -> bytes:
    """The original per-pixel implementation, kept as the specification."""
    bgra = bytearray(rgba_image.tobytes("raw", "BGRA"))
    scale = int(intensity * 255)
    for i in range(0, len(bgra), 4):
        a = bgra[i + 3] if intensity >= 1.0 else (bgra[i + 3] * scale) // 255
        bgra[i + 3] = a
        bgra[i] = (bgra[i] * a) // 255
        bgra[i + 1] = (bgra[i + 1] * a) // 255
        bgra[i + 2] = (bgra[i + 2] * a) // 255
    return bytes(bgra)


@pytest.mark.parametrize("intensity", [1.0, 1.5, 0.999, 0.5, 0.04, 0.0])
def test_matches_per_pixel_reference_on_random_pixels(intensity):
    image = Image.frombytes("RGBA", (64, 64), os.urandom(64 * 64 * 4))
    assert premultiplied_bgra(image, intensity) == _reference(image, intensity)


def test_every_alpha_and_colour_value():
    # 256 x 256: each row is one alpha value, each column one colour value.
    raw = bytes(v for a in range(256) for c in range(256) for v in (c, 255 - c, c // 2, a))
    image = Image.frombytes("RGBA", (256, 256), raw)
    for intensity in (1.0, 0.73, 0.2):
        assert premultiplied_bgra(image, intensity) == _reference(image, intensity)


def test_transparent_pixels_have_zero_colour():
    image = Image.new("RGBA", (4, 4), (255, 200, 100, 0))
    assert premultiplied_bgra(image, 1.0) == bytes(4 * 4 * 4)


def test_negative_intensity_is_fully_transparent():
    image = Image.new("RGBA", (2, 2), (255, 69, 0, 255))
    assert premultiplied_bgra(image, -0.5) == bytes(2 * 2 * 4)
