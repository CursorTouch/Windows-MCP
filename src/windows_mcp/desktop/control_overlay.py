"""Small, click-through Win32 indicators for an active AI desktop lease.

The display thread owns every HWND. Calls from MCP threads change desired state
and wait for an acknowledgement, so a screenshot cannot race a visible frame.
"""

from contextlib import contextmanager
import ctypes
import logging
import threading
import time
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFilter

from windows_mcp.desktop import flash_overlay
from windows_mcp import uia

logger = logging.getLogger(__name__)

_BORDER = 28
_CURSOR_SIZE = 88
_REFRESH_SECONDS = 0.05
_SW_HIDE = 0
_WDA_EXCLUDEFROMCAPTURE = 0x00000011
_BLUE = (45, 145, 255)
_AMBER = (255, 170, 55)

_user32 = ctypes.windll.user32
_user32.GetCursorPos.argtypes = [ctypes.POINTER(flash_overlay._POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL


def _edge_bitmap(width: int, height: int, side: str, color: tuple[int, int, int]) -> bytes:
    """Pre-render one inward gradient strip; cost scales with perimeter."""
    image = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(image)
    for depth in range(min(_BORDER, height if side in ("top", "bottom") else width)):
        alpha = int(190 * (1 - depth / _BORDER) ** 2)
        if side == "top":
            draw.line((0, depth, width - 1, depth), fill=(*color, alpha))
        elif side == "bottom":
            y = height - 1 - depth
            draw.line((0, y, width - 1, y), fill=(*color, alpha))
        elif side == "left":
            draw.line((depth, 0, depth, height - 1), fill=(*color, alpha))
        else:
            x = width - 1 - depth
            draw.line((x, 0, x, height - 1), fill=(*color, alpha))
    return flash_overlay._premultiplied_bgra(image, 1.0)


def _cursor_bitmap(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGBA", (_CURSOR_SIZE, _CURSOR_SIZE))
    draw = ImageDraw.Draw(image)
    inset = 17
    box = (inset, inset, _CURSOR_SIZE - inset - 1, _CURSOR_SIZE - inset - 1)
    draw.ellipse(box, outline=(*color, 210), width=5)
    glow = image.filter(ImageFilter.GaussianBlur(9))
    return flash_overlay._premultiplied_bgra(Image.alpha_composite(glow, image), 1.0)


class _Layer:
    def __init__(self, x: int, y: int, width: int, height: int, bgra: bytes, name: str):
        self.x, self.y, self.width, self.height = x, y, width, height
        self.class_name = f"WindowsMCPControl_{name}_{id(self):x}"
        self.hwnd, self.instance = flash_overlay._create_layered_window(
            self.class_name, x, y, width, height
        )
        try:
            flash_overlay._push_bitmap(self.hwnd, x, y, width, height, bgra)
            if not _user32.SetWindowDisplayAffinity(self.hwnd, _WDA_EXCLUDEFROMCAPTURE):
                # Own screenshots are protected by suspend_for_capture. Affinity
                # varies by Windows compositor and only affects other capturers.
                logger.warning("display affinity unavailable for AI control indicator")
        except BaseException:
            self.close()
            raise

    def show(self) -> None:
        _user32.ShowWindow(self.hwnd, flash_overlay._SW_SHOWNA)
        if not _user32.SetWindowPos(
            self.hwnd,
            flash_overlay._HWND_TOPMOST,
            0,
            0,
            0,
            0,
            flash_overlay._SWP_NOSIZE
            | flash_overlay._SWP_NOMOVE
            | flash_overlay._SWP_NOACTIVATE
            | flash_overlay._SWP_SHOWWINDOW,
        ) or not _user32.IsWindowVisible(self.hwnd):
            raise RuntimeError("AI control indicator window could not be shown")

    def hide(self) -> None:
        _user32.ShowWindow(self.hwnd, _SW_HIDE)
        if _user32.IsWindowVisible(self.hwnd):
            raise RuntimeError("AI control indicator window could not be hidden")

    def move(self, x: int, y: int) -> None:
        if (x, y) == (self.x, self.y):
            return
        if not _user32.SetWindowPos(
            self.hwnd,
            flash_overlay._HWND_TOPMOST,
            x,
            y,
            0,
            0,
            flash_overlay._SWP_NOSIZE | flash_overlay._SWP_NOACTIVATE,
        ):
            raise RuntimeError("AI control cursor indicator could not move")
        self.x, self.y = x, y

    def close(self) -> None:
        if self.hwnd:
            _user32.DestroyWindow(self.hwnd)
            _user32.UnregisterClassW(self.class_name, self.instance)
            self.hwnd = None


def _monitor_rects() -> tuple[tuple[int, int, int, int], ...]:
    return tuple((r.left, r.top, r.right, r.bottom) for r in uia.GetMonitorsRect())


def _build_layers(
    rects: tuple[tuple[int, int, int, int], ...], pending: bool
) -> tuple[list[_Layer], _Layer]:
    if not rects:
        raise RuntimeError("no display available for AI control indicator")
    color = _AMBER if pending else _BLUE
    layers: list[_Layer] = []
    try:
        for index, (left, top, right, bottom) in enumerate(rects):
            width, height = right - left, bottom - top
            if width <= 0 or height <= 0:
                raise RuntimeError("invalid display geometry for AI control indicator")
            border = min(_BORDER, width // 2, height // 2)
            if border <= 0:
                raise RuntimeError("display too small for AI control indicator")
            strips = (
                (left, top, width, border, "top"),
                (left, bottom - border, width, border, "bottom"),
                (left, top + border, border, height - 2 * border, "left"),
                (right - border, top + border, border, height - 2 * border, "right"),
            )
            for x, y, w, h, side in strips:
                if w and h:
                    layers.append(
                        _Layer(x, y, w, h, _edge_bitmap(w, h, side, color), f"{index}_{side}")
                    )
        point = flash_overlay._POINT()
        if not _user32.GetCursorPos(ctypes.byref(point)):
            raise RuntimeError("cannot locate cursor for AI control indicator")
        ring = _Layer(
            point.x - _CURSOR_SIZE // 2,
            point.y - _CURSOR_SIZE // 2,
            _CURSOR_SIZE,
            _CURSOR_SIZE,
            _cursor_bitmap(color),
            "cursor",
        )
        return layers, ring
    except BaseException:
        for layer in reversed(layers):
            layer.close()
        raise


class _Indicator:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.thread = threading.Thread(
            target=self._run, name="windows-mcp-control-indicator", daemon=True
        )
        self.active = False
        self.pending = False
        self.suspended = 0
        self.stopping = False
        self.version = 0
        self.applied = 0
        self.error: BaseException | None = None
        self.heartbeat = 0.0
        self.started = threading.Event()

    def start(self) -> None:
        self.thread.start()
        if not self.started.wait(timeout=3.0):
            raise RuntimeError("AI control indicator thread did not start")
        if self.error or not self.thread.is_alive():
            raise RuntimeError("AI control indicator thread failed at startup") from self.error

    def change(
        self, *, active: bool | None = None, pending: bool | None = None, suspended: int = 0
    ) -> None:
        with self.condition:
            if self.stopping:
                raise RuntimeError("AI control indicator is stopping")
            if self.error:
                raise RuntimeError("AI control indicator failed") from self.error
            if not self.thread.is_alive():
                raise RuntimeError("AI control indicator stopped")
            if active is not None:
                self.active = active
            if pending is not None:
                self.pending = pending
            self.suspended += suspended
            if self.suspended < 0:
                self.suspended = 0
                raise RuntimeError("unbalanced screenshot indicator suspension")
            self.version += 1
            version = self.version
            self.condition.notify_all()
            if not self.condition.wait_for(
                lambda: (
                    self.applied >= version
                    or self.stopping
                    or self.error is not None
                    or not self.thread.is_alive()
                ),
                timeout=3.0,
            ):
                # The owner may later recover and apply this hidden state.
                # Make the failure sticky so health checks can never grant a
                # new AI lease with an invisible indicator.
                self.error = RuntimeError("AI control indicator did not acknowledge state change")
                self.condition.notify_all()
                raise self.error
            if self.stopping:
                raise RuntimeError("AI control indicator is stopping")
            if self.error:
                raise RuntimeError("AI control indicator failed") from self.error

    def stop(self) -> None:
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        if self.thread.is_alive():
            self.thread.join(timeout=3.0)
        if self.thread.is_alive():
            raise RuntimeError("AI control indicator did not stop")

    def _run(self) -> None:
        layers: list[_Layer] = []
        ring: _Layer | None = None
        rects: tuple[tuple[int, int, int, int], ...] = ()
        mode: bool | None = None
        visible = False
        try:
            self.heartbeat = time.monotonic()
            self.started.set()
            while True:
                with self.condition:
                    self.condition.wait(timeout=_REFRESH_SECONDS)
                    if self.stopping:
                        return
                    active, pending = self.active, self.pending
                    suspended, version = self.suspended, self.version
                if active:
                    current_rects = _monitor_rects()
                    if ring is None or current_rects != rects or mode != pending:
                        for layer in reversed(layers):
                            layer.close()
                        layers = []
                        if ring:
                            ring.close()
                            ring = None
                        layers, ring = _build_layers(current_rects, pending)
                        rects, mode = current_rects, pending
                        visible = False
                    if suspended:
                        if visible:
                            for layer in layers:
                                layer.hide()
                            ring.hide()
                            visible = False
                    else:
                        if not visible:
                            for layer in layers:
                                layer.show()
                            ring.show()
                            visible = True
                        if not pending:
                            point = flash_overlay._POINT()
                            if _user32.GetCursorPos(ctypes.byref(point)):
                                ring.move(point.x - _CURSOR_SIZE // 2, point.y - _CURSOR_SIZE // 2)
                        else:
                            ring.hide()
                elif visible:
                    for layer in layers:
                        layer.hide()
                    if ring:
                        ring.hide()
                    visible = False
                # Pump messages on the owning thread, including display events.
                for layer in layers:
                    flash_overlay._pump_messages(layer.hwnd)
                if ring:
                    flash_overlay._pump_messages(ring.hwnd)
                with self.condition:
                    self.heartbeat = time.monotonic()
                    self.applied = version
                    self.condition.notify_all()
        except BaseException as exc:
            logger.exception("AI control indicator failed")
            with self.condition:
                self.error = exc
                self.condition.notify_all()
        finally:
            self.started.set()
            for layer in reversed(layers):
                layer.close()
            if ring:
                ring.close()


_lock = threading.Lock()
_capture_lock = threading.RLock()
_instance: _Indicator | None = None


def start() -> None:
    """Start the window owner thread, without displaying AI control yet."""
    global _instance
    with _capture_lock:
        with _lock:
            current = _instance
            if current is None:
                # Retain the reference before starting: a failed startup may
                # leave a live thread that must be stopped before retrying.
                indicator = _Indicator()
                _instance = indicator
            else:
                indicator = current
        if current is not None:
            if not is_healthy() or indicator.stopping:
                raise RuntimeError("Existing AI control indicator requires stop before restart")
            return
        try:
            indicator.start()
        except BaseException as startup_error:
            try:
                indicator.stop()
            except BaseException as cleanup_error:
                # A later stop() can retry the join; never stack a new HWND
                # owner on top of a potentially live failed one.
                raise RuntimeError(
                    "AI control indicator startup failed; cleanup is pending"
                ) from cleanup_error
            with _lock:
                if _instance is indicator:
                    _instance = None
            raise startup_error


def stop() -> None:
    """Destroy all indication windows and join the owner thread."""
    global _instance
    with _capture_lock:
        with _lock:
            indicator = _instance
        if indicator:
            indicator.stop()
            # The thread has exited and destroyed its windows. Only now may a
            # future start allocate another owner thread.
            with _lock:
                if _instance is indicator:
                    _instance = None


def set_active(active: bool) -> None:
    """Display or hide AI indication; activation failure raises."""

    # Deactivation must remain immediate during a capture: the screenshot's
    # final resume will then observe active=False and leave the window hidden.
    def apply() -> None:
        with _lock:
            indicator = _instance
        if indicator is None:
            if not active:
                return
            raise RuntimeError("AI control indicator has not started")
        indicator.change(active=active, pending=False)

    if active:
        with _capture_lock:
            apply()
    else:
        apply()


def set_pending(pending: bool) -> None:
    """Use amber edge indication without cursor following during takeover check."""
    with _capture_lock:
        with _lock:
            indicator = _instance
        if indicator:
            indicator.change(pending=pending)


def is_healthy() -> bool:
    """Reject an alive but stalled window owner after one second."""
    with _lock:
        indicator = _instance
    return bool(
        indicator
        and indicator.thread.is_alive()
        and not indicator.stopping
        and indicator.error is None
        and 0 <= time.monotonic() - indicator.heartbeat <= 1.0
    )


@contextmanager
def suspend_for_capture():
    """Serialize captures and wait for the indicator to disappear first."""
    with _capture_lock:
        with _lock:
            indicator = _instance
        if indicator:
            indicator.change(suspended=1)
        try:
            yield
        finally:
            if indicator:
                indicator.change(suspended=-1)
