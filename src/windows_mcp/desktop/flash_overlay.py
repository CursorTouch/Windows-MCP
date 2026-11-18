"""Brief on-screen visual confirmation that a screenshot was taken.

Renders a soft orange-red glow halo around the captured area for ~3.5 s
using Win32 layered windows with per-pixel alpha (``UpdateLayeredWindow``
+ premultiplied BGRA DIB section). The glow only reaches ~50 px in from
each edge, so it is drawn as four edge-strip windows per rect rather than
one window covering the whole desktop; the strip pixels are cached per
display layout, uploaded once per flash, and faded by changing only the
windows' constant alpha. Tk was tried twice and abandoned —
``-transparentcolor`` rendered nothing on some Windows configs and a
multi-Toplevel "strip" approach hung Tk's mainloop on a non-main thread
once more than ~6 windows were created back-to-back.

The flash is started *after* capture and any active overlay is torn down
before the next capture so it never appears in a captured image.
"""

import ctypes
import functools
import logging
import os
import threading
import time
from ctypes import wintypes
from typing import NamedTuple

from windows_mcp.desktop.overlay_bitmap import premultiplied_bgra as _premultiplied_bgra

logger = logging.getLogger(__name__)

_FLASH_RGB = (0xFF, 0x45, 0x00)
_DURATION_MS = 3500
_FRAME_INTERVAL_MS = 30
_GLOW_BORDER_THICKNESS = 8
_GLOW_BLUR_RADIUS = 14
_FULLSCREEN_INSET = 6
# Opacity levels per fade direction: ~32 opacity changes per flash in total.
# Each change only swaps the windows' constant alpha (no pixel upload), so a
# finer fade costs one cheap UpdateLayeredWindow call per strip per level.
_OPACITY_LEVELS = 16
# The blurred ring's alpha reaches 0 at most 49 px in from a rect edge
# (inset 6 + ring 8 + blur tail 35), so a 56 px strip holds every glow pixel.
_STRIP_THICKNESS = 56
# Gaussian blur support; strips are rendered with this much context around
# them so cropping never changes a pixel compared with a full-canvas render.
_BLUR_MARGIN = 4 * _GLOW_BLUR_RADIUS
_GLOW_CACHE_SIZE = 4

_lock = threading.Lock()
_active_overlay: "_Overlay | None" = None
_all_overlays: set["_Overlay"] = set()


def _flash_disabled() -> bool:
    value = os.getenv("WINDOWS_MCP_DISABLE_FLASH", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


class _Overlay:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.closed_event = threading.Event()
        self.thread: threading.Thread | None = None


def cancel_active_flash(timeout: float = 0.25) -> bool:
    """Stop every pending flash and report whether all windows have closed.

    A replaced flash can still be winding down while a newer one is active;
    screenshot capture must wait for both rather than just the latest overlay.
    """
    global _active_overlay
    with _lock:
        overlays = set(_all_overlays)
        if _active_overlay is not None:
            overlays.add(_active_overlay)
        _active_overlay = None
    for overlay in overlays:
        overlay.stop_event.set()
    deadline = time.monotonic() + timeout
    return all(
        overlay.closed_event.wait(timeout=max(0.0, deadline - time.monotonic()))
        for overlay in overlays
    )


def show_capture_flash(capture_rect: "object | None" = None) -> None:
    """Show a fade-in/out orange-red glow for a screenshot capture.

    Pass the same ``capture_rect`` (``uia.Rect`` with ``left/top/right/
    bottom``) used for the screenshot — or ``None`` for a full-desktop
    capture, in which case every monitor gets an inner-edge halo. Returns
    immediately; rendering happens on a daemon thread, and any previously
    active overlay is cancelled atomically so a follow-up capture never
    leaks a stale overlay.
    """
    if _flash_disabled():
        return
    try:
        if capture_rect is not None:
            rects: list[tuple[int, int, int, int]] = [
                (
                    int(capture_rect.left),
                    int(capture_rect.top),
                    int(capture_rect.right),
                    int(capture_rect.bottom),
                )
            ]
            full_screen = False
        else:
            import windows_mcp.uia as uia

            monitor_rects = uia.GetMonitorsRect()
            rects = [(m.left, m.top, m.right, m.bottom) for m in monitor_rects]
            full_screen = True
    except Exception:
        logger.debug("could not resolve flash overlay rects", exc_info=True)
        return
    if not rects:
        return
    overlay = _Overlay()
    overlay.thread = threading.Thread(
        target=_run_overlay,
        args=(rects, full_screen, overlay),
        name="windows-mcp-flash",
        daemon=True,
    )
    # Atomic swap: save the previous overlay under the lock, install the new
    # one, then signal the prior overlay outside the lock so it can tear down
    # without blocking new callers. Without this, an overwrite would orphan
    # the prior overlay — cancel_active_flash() could no longer reach it.
    with _lock:
        global _active_overlay
        prev = _active_overlay
        _active_overlay = overlay
        _all_overlays.add(overlay)
    if prev is not None:
        prev.stop_event.set()
    try:
        overlay.thread.start()
    except BaseException:
        with _lock:
            _all_overlays.discard(overlay)
            if _active_overlay is overlay:
                _active_overlay = None
        overlay.closed_event.set()
        raise


# ---------------------------------------------------------------------------
# Win32 plumbing
# ---------------------------------------------------------------------------

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_kernel32 = ctypes.windll.kernel32

_WS_POPUP = 0x80000000
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_ULW_ALPHA = 0x00000002
_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01
_BI_RGB = 0
_DIB_RGB_COLORS = 0
_SW_SHOWNA = 8
_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_PM_REMOVE = 0x0001
_WM_DESTROY = 0x0002


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", ctypes.c_uint32 * 3),
    ]


# LRESULT is signed pointer-sized integer on Windows (use c_ssize_t for x64).
_LRESULT = ctypes.c_ssize_t

_WNDPROC = ctypes.WINFUNCTYPE(
    _LRESULT,
    wintypes.HWND,
    ctypes.c_uint,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class _WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


_user32.CreateWindowExW.restype = wintypes.HWND
_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
_user32.RegisterClassExW.restype = ctypes.c_ushort
_user32.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASSEX)]
_user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
_user32.UnregisterClassW.restype = wintypes.BOOL
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_user32.DefWindowProcW.restype = _LRESULT
_user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    ctypes.c_uint,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.UpdateLayeredWindow.restype = wintypes.BOOL
_user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND,
    wintypes.HDC,
    ctypes.POINTER(_POINT),
    ctypes.POINTER(_SIZE),
    wintypes.HDC,
    ctypes.POINTER(_POINT),
    wintypes.COLORREF,
    ctypes.POINTER(_BLENDFUNCTION),
    wintypes.DWORD,
]
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(_BITMAPINFO),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_glow_rgba(
    width: int,
    height: int,
    rect_list: list[tuple[int, int, int, int]],
    *,
    outward: bool = True,
) -> "object":
    """Return a PIL RGBA image with a soft halo ring around each rect.

    Each rect is in window-local coordinates. A sharp solid border is drawn
    just outside the rect edge (``outward=True``) so the captured area stays
    clean and the halo reads as a surround, then the layer is
    gaussian-blurred to spread the glow, and the sharp ring is composited
    back on top so the inner edge stays crisp. ``outward=False`` nests the
    ring inward — used for the full-screen inner halo.
    """
    from PIL import Image, ImageDraw, ImageFilter

    sharp = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sharp)
    color = (*_FLASH_RGB, 255)
    for x1, y1, x2, y2 in rect_list:
        for i in range(_GLOW_BORDER_THICKNESS):
            if outward:
                draw.rectangle(
                    [x1 - i - 1, y1 - i - 1, x2 + i, y2 + i],
                    outline=color,
                    width=1,
                )
            else:
                draw.rectangle(
                    [x1 + i, y1 + i, x2 - i - 1, y2 - i - 1],
                    outline=color,
                    width=1,
                )
    blurred = sharp.filter(ImageFilter.GaussianBlur(radius=_GLOW_BLUR_RADIUS))
    return Image.alpha_composite(blurred, sharp)


class _Strip(NamedTuple):
    """One edge window of the glow: screen position, size and BGRA pixels."""

    x: int
    y: int
    width: int
    height: int
    bgra: bytes


def _edge_strip_rects(
    rect: tuple[int, int, int, int], thickness: int
) -> list[tuple[int, int, int, int]]:
    """Split a rect's border band into non-overlapping (x, y, w, h) strips.

    The top and bottom strips span the full width and so own the corners;
    the left and right strips fill only the height between them. A rect
    smaller than two thicknesses is covered by its top and bottom strips.
    """
    left, top, right, bottom = rect
    width, height = right - left, bottom - top
    top_h = min(thickness, height)
    bottom_h = min(thickness, height - top_h)
    side_h = height - top_h - bottom_h
    left_w = min(thickness, width)
    right_w = min(thickness, width - left_w)
    strips = [
        (left, top, width, top_h),
        (left, bottom - bottom_h, width, bottom_h),
        (left, top + top_h, left_w, side_h),
        (right - right_w, top + top_h, right_w, side_h),
    ]
    return [s for s in strips if s[2] > 0 and s[3] > 0]


@functools.lru_cache(maxsize=_GLOW_CACHE_SIZE)
def _glow_strips(
    rects: tuple[tuple[int, int, int, int], ...], full_screen: bool
) -> tuple[_Strip, ...]:
    """Render the glow as premultiplied edge strips, cached per layout.

    The result is pixel-identical, on every displayed pixel, to rendering one
    bitmap over the union of ``rects`` and cropping it: each strip is drawn on
    a canvas that extends ``_BLUR_MARGIN`` past it (clipped to the union, as
    the full render was), so the blur sees the same neighbourhood. The pixels
    depend only on the screen rects and the module's fixed colour and size
    constants, so ``(rects, full_screen)`` is the whole cache key; physical
    monitor rects already reflect DPI scaling.
    """
    union_left = min(r[0] for r in rects)
    union_top = min(r[1] for r in rects)
    union_right = max(r[2] for r in rects)
    union_bottom = max(r[3] for r in rects)
    inset = _FULLSCREEN_INSET if full_screen else 0
    rings = [(r[0] + inset, r[1] + inset, r[2] - inset, r[3] - inset) for r in rects]

    strips: list[_Strip] = []
    for rect in rects:
        for x, y, w, h in _edge_strip_rects(rect, _STRIP_THICKNESS):
            canvas_left = max(union_left, x - _BLUR_MARGIN)
            canvas_top = max(union_top, y - _BLUR_MARGIN)
            canvas_right = min(union_right, x + w + _BLUR_MARGIN)
            canvas_bottom = min(union_bottom, y + h + _BLUR_MARGIN)
            local_rings = [
                (rl - canvas_left, rt - canvas_top, rr - canvas_left, rb - canvas_top)
                for rl, rt, rr, rb in rings
            ]
            canvas = _render_glow_rgba(
                canvas_right - canvas_left,
                canvas_bottom - canvas_top,
                local_rings,
                outward=False,
            )
            crop = canvas.crop(
                (x - canvas_left, y - canvas_top, x - canvas_left + w, y - canvas_top + h)
            )
            strips.append(_Strip(x, y, w, h, _premultiplied_bgra(crop, 1.0)))
    return tuple(strips)


# ---------------------------------------------------------------------------
# Window management
# ---------------------------------------------------------------------------


@_WNDPROC
def _wnd_proc(hwnd: int | None, msg: int, wparam: int, lparam: int) -> int:
    # ctypes hands a NULL HWND (c_void_p) to the callback as None.
    if msg == _WM_DESTROY:
        _user32.PostQuitMessage(0)
        return 0
    return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _create_layered_window(
    class_name: str, x: int, y: int, w: int, h: int
) -> tuple[int, int | None]:
    # HWND/HMODULE restypes are c_void_p, so NULL comes back as None. The HWND
    # is checked below; the module handle is only passed back to Win32.
    h_instance = _kernel32.GetModuleHandleW(None)
    wc = _WNDCLASSEX()
    wc.cbSize = ctypes.sizeof(_WNDCLASSEX)
    wc.style = 0
    wc.lpfnWndProc = _wnd_proc
    wc.cbClsExtra = 0
    wc.cbWndExtra = 0
    wc.hInstance = h_instance
    wc.hIcon = None
    wc.hCursor = None
    wc.hbrBackground = None
    wc.lpszMenuName = None
    wc.lpszClassName = class_name
    wc.hIconSm = None

    atom = _user32.RegisterClassExW(ctypes.byref(wc))
    if not atom:
        raise OSError(f"RegisterClassExW failed: {ctypes.get_last_error()}")

    ex_style = (
        _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE
    )
    hwnd = _user32.CreateWindowExW(
        ex_style,
        class_name,
        "windows-mcp-flash",
        _WS_POPUP,
        x,
        y,
        w,
        h,
        None,
        None,
        h_instance,
        None,
    )
    if not hwnd:
        _user32.UnregisterClassW(class_name, h_instance)
        raise OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}")
    return hwnd, h_instance


def _push_bitmap(
    hwnd: int, x: int, y: int, w: int, h: int, bgra: bytes, *, opacity: int = 255
) -> None:
    """Upload premultiplied pixels with optional whole-window opacity."""
    screen_dc = _user32.GetDC(None)
    if not screen_dc:
        raise OSError("GetDC failed")
    try:
        mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
        if not mem_dc:
            raise OSError("CreateCompatibleDC failed")
        try:
            bmi = _BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h  # top-down DIB
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = _BI_RGB

            bits_ptr = ctypes.c_void_p()
            hbm = _gdi32.CreateDIBSection(
                screen_dc,
                ctypes.byref(bmi),
                _DIB_RGB_COLORS,
                ctypes.byref(bits_ptr),
                None,
                0,
            )
            if not hbm:
                raise OSError("CreateDIBSection failed")
            try:
                ctypes.memmove(bits_ptr, bgra, len(bgra))
                old_bmp = _gdi32.SelectObject(mem_dc, hbm)
                try:
                    pos = _POINT(x, y)
                    size = _SIZE(w, h)
                    src_pos = _POINT(0, 0)
                    blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, opacity, _AC_SRC_ALPHA)
                    ok = _user32.UpdateLayeredWindow(
                        hwnd,
                        screen_dc,
                        ctypes.byref(pos),
                        ctypes.byref(size),
                        mem_dc,
                        ctypes.byref(src_pos),
                        0,
                        ctypes.byref(blend),
                        _ULW_ALPHA,
                    )
                    if not ok:
                        raise OSError(f"UpdateLayeredWindow failed: {ctypes.get_last_error()}")
                finally:
                    _gdi32.SelectObject(mem_dc, old_bmp)
            finally:
                _gdi32.DeleteObject(hbm)
        finally:
            _gdi32.DeleteDC(mem_dc)
    finally:
        _user32.ReleaseDC(None, screen_dc)


def _set_window_opacity(hwnd: int, opacity: int) -> None:
    """Fade a layered window's already-uploaded per-pixel bitmap.

    ``UpdateLayeredWindow`` keeps the current surface when ``hdcSrc`` (and
    the position and size) are NULL, so only the blend function changes and
    no pixels are copied. ``SetLayeredWindowAttributes`` is not used: it can
    switch the window away from its per-pixel bitmap.
    """
    blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, opacity, _AC_SRC_ALPHA)
    ok = _user32.UpdateLayeredWindow(
        hwnd, None, None, None, None, None, 0, ctypes.byref(blend), _ULW_ALPHA
    )
    if not ok:
        raise OSError(f"UpdateLayeredWindow failed: {ctypes.get_last_error()}")


def _pump_messages(hwnd: int) -> None:
    msg = wintypes.MSG()
    while _user32.PeekMessageW(ctypes.byref(msg), hwnd, 0, 0, _PM_REMOVE):
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))


def _intensity_at(t_norm: float, full_screen: bool) -> float:
    if full_screen:
        return 1.0 - abs(2 * t_norm - 1)
    if t_norm < 0.15:
        return t_norm / 0.15
    if t_norm < 0.65:
        return 1.0
    return max(0.0, 1.0 - (t_norm - 0.65) / 0.35)


# ---------------------------------------------------------------------------
# Daemon thread entry point
# ---------------------------------------------------------------------------


def _run_overlay(
    rects: list[tuple[int, int, int, int]],
    full_screen: bool,
    overlay: _Overlay,
) -> None:
    try:
        from PIL import Image  # noqa: F401  — fail fast if Pillow missing
    except Exception:
        logger.debug("Pillow unavailable; skipping screenshot flash")
        with _lock:
            global _active_overlay
            if _active_overlay is overlay:
                _active_overlay = None
            _all_overlays.discard(overlay)
        overlay.closed_event.set()
        return

    windows: list[tuple[int, int | None, str]] = []
    class_prefix = f"WindowsMCPFlash_{id(overlay):x}"

    try:
        # Identical rects (e.g. a duplicated display) would draw the glow twice.
        layout = tuple(dict.fromkeys(r for r in rects if r[2] > r[0] and r[3] > r[1]))
        if not layout:
            return
        strips = _glow_strips(layout, full_screen)

        for index, strip in enumerate(strips):
            class_name = f"{class_prefix}_{index}"
            hwnd, h_instance = _create_layered_window(
                class_name, strip.x, strip.y, strip.width, strip.height
            )
            windows.append((hwnd, h_instance, class_name))
            # The only pixel upload of this flash, made fully transparent so
            # showing the window cannot flash the glow at full strength.
            _push_bitmap(hwnd, strip.x, strip.y, strip.width, strip.height, strip.bgra, opacity=0)
            _user32.ShowWindow(hwnd, _SW_SHOWNA)
            _user32.SetWindowPos(
                hwnd,
                _HWND_TOPMOST,
                0,
                0,
                0,
                0,
                _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW,
            )

        logger.info(
            "screenshot flash overlay started: %d edge window(s), %d px, for %d rect(s)",
            len(strips),
            sum(s.width * s.height for s in strips),
            len(layout),
        )

        start = time.perf_counter()
        level = 0
        while not overlay.stop_event.is_set():
            elapsed_ms = (time.perf_counter() - start) * 1000
            if elapsed_ms >= _DURATION_MS:
                # A late last frame can leave the fade short of zero; finish it
                # so the windows never vanish at a visible level.
                if level != 0:
                    for hwnd, _, _ in windows:
                        _set_window_opacity(hwnd, 0)
                break
            intensity = _intensity_at(elapsed_ms / _DURATION_MS, full_screen)
            target = round(intensity * _OPACITY_LEVELS)
            if target != level:
                opacity = round(target * 255 / _OPACITY_LEVELS)
                for hwnd, _, _ in windows:
                    _set_window_opacity(hwnd, opacity)
                level = target
            for hwnd, _, _ in windows:
                _pump_messages(hwnd)
            # Waiting on the stop event lets a newer capture end the flash at once.
            overlay.stop_event.wait(_FRAME_INTERVAL_MS / 1000)
    except Exception:
        logger.debug("screenshot flash overlay failed", exc_info=True)
    finally:
        for hwnd, h_instance, class_name in reversed(windows):
            try:
                _user32.DestroyWindow(hwnd)
            except Exception:
                pass
            try:
                _user32.UnregisterClassW(class_name, h_instance)
            except Exception:
                pass
        with _lock:
            if _active_overlay is overlay:
                _active_overlay = None
            _all_overlays.discard(overlay)
        overlay.closed_event.set()
