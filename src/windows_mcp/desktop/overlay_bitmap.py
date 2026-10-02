"""Shared pixel conversion for the control indicator and capture flash."""


def premultiplied_bgra(rgba_image, intensity: float) -> bytes:
    """Convert a PIL RGBA image to scaled, premultiplied BGRA bytes."""
    bgra = bytearray(rgba_image.tobytes("raw", "BGRA"))
    if intensity >= 1.0:
        for i in range(0, len(bgra), 4):
            a = bgra[i + 3]
            if a == 0:
                continue
            bgra[i] = (bgra[i] * a) // 255
            bgra[i + 1] = (bgra[i + 1] * a) // 255
            bgra[i + 2] = (bgra[i + 2] * a) // 255
    else:
        for i in range(0, len(bgra), 4):
            a = (bgra[i + 3] * int(intensity * 255)) // 255
            bgra[i + 3] = a
            if a == 0:
                bgra[i] = 0
                bgra[i + 1] = 0
                bgra[i + 2] = 0
                continue
            bgra[i] = (bgra[i] * a) // 255
            bgra[i + 1] = (bgra[i + 1] * a) // 255
            bgra[i + 2] = (bgra[i + 2] * a) // 255
    return bytes(bgra)
