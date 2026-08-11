"""Brief on-screen visual confirmation that a screenshot was taken.

Renders a soft orange-red glow halo around the captured area for ~2.5 s
using a Win32 layered window with per-pixel alpha (``UpdateLayeredWindow``
+ premultiplied BGRA DIB section). Tk was tried twice and abandoned —
``-transparentcolor`` rendered nothing on some Windows configs and a
multi-Toplevel "strip" approach hung Tk's mainloop on a non-main thread
once more than ~6 windows were created back-to-back.

The flash is started *after* capture and any active overlay is torn down
before the next capture so it never appears in a captured image.
"""

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

_FLASH_RGB = (0xFF, 0x45, 0x00)
_DURATION_MS = 3500
_FRAME_INTERVAL_MS = 30
_GLOW_BORDER_THICKNESS = 8
_GLOW_BLUR_RADIUS = 14
_FULLSCREEN_INSET = 6
_MIN_VISIBLE_INTENSITY = 0.04
_INTENSITY_QUANT = 32

_lock = threading.Lock()
_active_overlay: "_Overlay | None" = None


def _flash_disabled() -> bool:
    value = os.getenv("WINDOWS_MCP_DISABLE_FLASH", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


class _Overlay:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.closed_event = threading.Event()
        self.thread: threading.Thread | None = None


def cancel_active_flash(timeout: float = 0.25) -> None:
    """Tear down any flash overlay currently on screen."""
    global _active_overlay
    with _lock:
        ov = _active_overlay
        _active_overlay = None
    if ov is None:
        return
    ov.stop_event.set()
    ov.closed_event.wait(timeout=timeout)


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
    if prev is not None:
        prev.stop_event.set()
    overlay.thread.start()


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
_user32.RegisterClassExW.restype = ctypes.c_ushort
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


def _premultiplied_bgra(rgba_image, intensity: float) -> bytes:
    """Convert PIL RGBA to BGRA premultiplied bytes scaled by ``intensity``."""
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


# ---------------------------------------------------------------------------
# Window management
# ---------------------------------------------------------------------------


@_WNDPROC
def _wnd_proc(hwnd, msg, wparam, lparam):
    if msg == _WM_DESTROY:
        _user32.PostQuitMessage(0)
        return 0
    return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _create_layered_window(class_name: str, x: int, y: int, w: int, h: int):
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


def _push_bitmap(hwnd, x: int, y: int, w: int, h: int, bgra: bytes) -> None:
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
                    blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
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


def _pump_messages(hwnd) -> None:
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
        overlay.closed_event.set()
        return

    hwnd = None
    h_instance = None
    class_name = f"WindowsMCPFlash_{id(overlay):x}"

    try:
        union_left = min(r[0] for r in rects)
        union_top = min(r[1] for r in rects)
        union_right = max(r[2] for r in rects)
        union_bottom = max(r[3] for r in rects)
        width = union_right - union_left
        height = union_bottom - union_top
        if width <= 0 or height <= 0:
            return

        local_rects = []
        for r_left, r_top, r_right, r_bottom in rects:
            inset = _FULLSCREEN_INSET if full_screen else 0
            local_rects.append(
                (
                    r_left - union_left + inset,
                    r_top - union_top + inset,
                    r_right - union_left - inset,
                    r_bottom - union_top - inset,
                )
            )

        hwnd, h_instance = _create_layered_window(class_name, union_left, union_top, width, height)
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

        glow_rgba = _render_glow_rgba(width, height, local_rects, outward=False)

        logger.info(
            "screenshot flash overlay started: %dx%d layered window at (%d,%d) for %d rect(s)",
            width,
            height,
            union_left,
            union_top,
            len(rects),
        )

        start = time.perf_counter()
        last_intensity_q = -1
        while not overlay.stop_event.is_set():
            elapsed_ms = (time.perf_counter() - start) * 1000
            if elapsed_ms >= _DURATION_MS:
                break
            intensity = _intensity_at(elapsed_ms / _DURATION_MS, full_screen)
            intensity_q = round(intensity * _INTENSITY_QUANT)
            if intensity_q != last_intensity_q:
                if intensity < _MIN_VISIBLE_INTENSITY:
                    bgra = b"\x00" * (width * height * 4)
                else:
                    bgra = _premultiplied_bgra(glow_rgba, intensity)
                _push_bitmap(hwnd, union_left, union_top, width, height, bgra)
                last_intensity_q = intensity_q
            _pump_messages(hwnd)
            time.sleep(_FRAME_INTERVAL_MS / 1000)
    except Exception:
        logger.debug("screenshot flash overlay failed", exc_info=True)
    finally:
        try:
            if hwnd:
                _user32.DestroyWindow(hwnd)
        except Exception:
            pass
        try:
            if h_instance:
                _user32.UnregisterClassW(class_name, h_instance)
        except Exception:
            pass
        with _lock:
            global _active_overlay
            if _active_overlay is overlay:
                _active_overlay = None
        overlay.closed_event.set()
