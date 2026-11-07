"""Shared pixel conversion for the control indicator and capture flash."""

from PIL import Image, ImageChops


def premultiplied_bgra(rgba_image: Image.Image, intensity: float) -> bytes:
    """Convert a PIL RGBA image to scaled, premultiplied BGRA bytes.

    The work runs in Pillow's C channel operations rather than a Python loop
    over every pixel. A full-screen halo is over a million pixels; the loop
    took 250-400 ms per frame while holding the GIL, which stalled screenshot
    capture and left the previous flash unable to close in time.

    ``ImageChops.multiply`` computes ``a * b // 255``, and the alpha lookup
    table computes ``a * int(intensity * 255) // 255``, so the bytes are the
    same as the per-pixel arithmetic this replaces. A pixel with alpha 0 gets
    zero RGB, which layered windows require.

    Args:
        rgba_image: Source RGBA bitmap.
        intensity: Opacity multiplier for the layered window.

    Returns:
        Premultiplied BGRA pixels in row-major order.
    """
    red, green, blue, alpha = rgba_image.split()
    if intensity < 1.0:
        scale = max(0, int(intensity * 255))
        alpha = alpha.point([(value * scale) // 255 for value in range(256)])
    red, green, blue = (ImageChops.multiply(channel, alpha) for channel in (red, green, blue))
    return Image.merge("RGBA", (red, green, blue, alpha)).tobytes("raw", "BGRA")
