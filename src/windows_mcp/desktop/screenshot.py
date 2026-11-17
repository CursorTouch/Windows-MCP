from dataclasses import dataclass
import ctypes
import logging
import os
import threading
import time
from _ctypes import COMError

from PIL import Image, ImageGrab

try:
    import dxcam
except Exception:
    dxcam = None

try:
    import mss
except ImportError:
    mss = None

import windows_mcp.uia as uia

#: How long a DXGI capture waits for a frame newer than the last one it saw.
_NEW_FRAME_WAIT_SECONDS = 0.25

#: How long the dxcam backend keeps a camera and the last full frame it returned
#: after the most recent capture on that output. The frame is only needed when a
#: capture follows the previous one before the desktop changes (DXGI then has no
#: new frame). Once it is dropped, the camera is released with it, and the next
#: capture creates a new duplication, whose first frame is the current desktop:
#: the same picture at the cost of one camera setup (~0.1 s). A frame is 6-25 MB
#: per output (1080p-4K), so it is only kept across back-to-back captures, not
#: across the seconds an agent spends between tool calls.
_LAST_FRAME_TTL_SECONDS = 5.0


def _dwm_flush() -> None:
    """Wait for the next desktop composition pass, so hidden windows are gone."""
    try:
        ctypes.windll.dwmapi.DwmFlush()
    except (AttributeError, OSError):
        pass


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DxcamOutput:
    device_idx: int
    output_idx: int
    rect: uia.Rect


def _build_crop_box(capture_rect: uia.Rect, padding: int = 0) -> tuple[int, int, int, int]:
    left_offset, top_offset, _, _ = uia.GetVirtualScreenRect()
    return (
        capture_rect.left - left_offset + padding,
        capture_rect.top - top_offset + padding,
        capture_rect.right - left_offset + padding,
        capture_rect.bottom - top_offset + padding,
    )


def _crop_screenshot(screenshot: Image.Image, capture_rect: uia.Rect | None) -> Image.Image:
    if capture_rect is None:
        return screenshot
    return screenshot.crop(_build_crop_box(capture_rect))


def get_screenshot_backend() -> str:
    """Read the preferred backend from the environment variable."""
    value = os.getenv("WINDOWS_MCP_SCREENSHOT_BACKEND", "auto")
    normalized = value.strip().lower()
    valid = _ScreenshotBackend.registry.keys() | {"auto"}
    if normalized in valid:
        return normalized
    logger.warning(
        "Unknown screenshot backend '%s'; falling back to auto",
        value,
    )
    return "auto"


# ---------------------------------------------------------------------------
# Backend framework
# ---------------------------------------------------------------------------


class _ScreenshotBackend:
    """Base class for screenshot capture backends.

    Subclasses **must** define two class attributes:

    * ``name: str`` – unique key such as ``"dxcam"``.
    * ``priority: int`` – lower numbers are tried first in the *auto* chain.

    Defining both attributes automatically registers the subclass via
    ``__init_subclass__``.
    """

    name: str
    priority: int

    registry: dict[str, type["_ScreenshotBackend"]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if "name" in cls.__dict__ and "priority" in cls.__dict__:
            existing = _ScreenshotBackend.registry.get(cls.name)
            if existing is not None and existing is not cls:
                raise ValueError(f"Duplicate screenshot backend name: {cls.name!r}")
            _ScreenshotBackend.registry[cls.name] = cls

    def is_available(self, capture_rect: uia.Rect | None) -> bool:
        """Return ``True`` if this backend can service the request."""
        return True

    def capture(self, capture_rect: uia.Rect | None) -> Image.Image:
        """Capture a screenshot.  Subclasses must override."""
        raise NotImplementedError


class _DxcamBackend(_ScreenshotBackend):
    """DXGI-based capture via the *dxcam* library."""

    name = "dxcam"
    priority = 10

    def __init__(self) -> None:
        self._camera_cache: dict[tuple[int, int], object] = {}
        # Last full output frame each camera returned. DXGI only hands out a frame
        # when the desktop changed since the previous acquire, so this is the
        # current picture whenever no new frame arrives.
        self._last_frames: dict[tuple[int, int], object] = {}
        # When each output was last captured, by self._clock.
        self._last_used: dict[tuple[int, int], float] = {}
        self._clock = time.monotonic
        # Held for a whole capture and for every release, so a camera is never
        # released (by the expiry timer or a display change) while it is in use.
        self._lock = threading.RLock()
        self._expiry_timer: threading.Timer | None = None
        # The DXGI output layout the cached cameras were created for.
        self._layout: tuple[tuple[int, int, int, int, int, int], ...] | None = None

    @staticmethod
    def _iter_outputs() -> list[DxcamOutput]:
        if dxcam is None:
            return []

        factory = getattr(dxcam, "__factory", None)
        if factory is None:
            return []

        outputs: list[DxcamOutput] = []
        for device_idx, device_outputs in enumerate(getattr(factory, "outputs", [])):
            for output_idx, output in enumerate(device_outputs):
                try:
                    output.update_desc()
                    coordinates = output.desc.DesktopCoordinates
                    if not output.attached_to_desktop:
                        continue
                except (AttributeError, OSError, RuntimeError, ValueError, COMError):
                    logger.debug(
                        "Failed to read DXGI output geometry for device=%s output=%s",
                        device_idx,
                        output_idx,
                        exc_info=True,
                    )
                    continue
                outputs.append(
                    DxcamOutput(
                        device_idx=device_idx,
                        output_idx=output_idx,
                        rect=uia.Rect(
                            coordinates.left,
                            coordinates.top,
                            coordinates.right,
                            coordinates.bottom,
                        ),
                    )
                )
        return outputs

    @classmethod
    def _resolve_region(
        cls,
        capture_rect: uia.Rect,
    ) -> tuple[int, int, tuple[int, int, int, int] | None] | None:
        """Return ``(device_idx, output_idx, region)`` when one DXGI output contains the rect."""
        for output in cls._iter_outputs():
            output_rect = output.rect
            if (
                output_rect.left <= capture_rect.left
                and output_rect.top <= capture_rect.top
                and output_rect.right >= capture_rect.right
                and output_rect.bottom >= capture_rect.bottom
            ):
                if output_rect == capture_rect:
                    return output.device_idx, output.output_idx, None
                return (
                    output.device_idx,
                    output.output_idx,
                    (
                        capture_rect.left - output_rect.left,
                        capture_rect.top - output_rect.top,
                        capture_rect.right - output_rect.left,
                        capture_rect.bottom - output_rect.top,
                    ),
                )
        return None

    def is_available(self, capture_rect: uia.Rect | None) -> bool:
        if dxcam is None:
            return False
        if capture_rect is None:
            return False
        return self._resolve_region(capture_rect) is not None

    def _get_camera(self, device_idx: int, output_idx: int) -> object:
        camera_key = (device_idx, output_idx)
        camera = self._camera_cache.get(camera_key)
        if camera is None:
            camera = dxcam.create(
                device_idx=device_idx,
                output_idx=output_idx,
                processor_backend="numpy",
            )
            self._camera_cache[camera_key] = camera
        return camera

    def _release_camera(self, camera_key: tuple[int, int]) -> None:
        """Release one cached camera and forget its last frame."""
        self._last_frames.pop(camera_key, None)
        self._last_used.pop(camera_key, None)
        camera = self._camera_cache.pop(camera_key, None)
        if camera is None:
            return
        try:
            camera.release()
        except Exception:
            logger.debug("Failed to release dxcam camera %s", camera_key, exc_info=True)

    def release_expired(self) -> None:
        """Release cameras and frames not used within ``_LAST_FRAME_TTL_SECONDS``."""
        with self._lock:
            now = self._clock()
            for camera_key, last_used in list(self._last_used.items()):
                if now - last_used >= _LAST_FRAME_TTL_SECONDS:
                    self._release_camera(camera_key)

    def _release_on_layout_change(self, outputs: list[DxcamOutput]) -> None:
        """Release every camera when outputs were added, removed, moved or resized.

        A camera and its last frame describe one output's old geometry: reusing
        them after a change crops the wrong pixels (or the wrong size) whenever no
        new frame arrives.
        """
        layout = tuple(
            (o.device_idx, o.output_idx, o.rect.left, o.rect.top, o.rect.right, o.rect.bottom)
            for o in outputs
        )
        if self._layout is not None and layout != self._layout:
            logger.debug("DXGI output layout changed; releasing cached dxcam cameras")
            for camera_key in list(self._camera_cache):
                self._release_camera(camera_key)
        self._layout = layout

    def _schedule_expiry(self) -> None:
        """Arm one timer for the oldest camera, so an idle process frees it too."""
        with self._lock:
            if self._expiry_timer is not None or not self._last_used:
                return
            oldest = min(self._last_used.values())
            delay = max(0.01, oldest + _LAST_FRAME_TTL_SECONDS - self._clock())
            timer = threading.Timer(delay, self._on_expiry_timer)
            timer.daemon = True
            self._expiry_timer = timer
            timer.start()

    def _on_expiry_timer(self) -> None:
        with self._lock:
            self._expiry_timer = None
            self.release_expired()
            self._schedule_expiry()

    def capture(self, capture_rect: uia.Rect | None) -> Image.Image:
        with self._lock:
            try:
                return self._capture_locked(capture_rect)
            finally:
                self._schedule_expiry()

    def _capture_locked(self, capture_rect: uia.Rect | None) -> Image.Image:
        self._release_on_layout_change(self._iter_outputs())
        resolved = self._resolve_region(capture_rect)
        if resolved is None:
            raise ValueError(
                "DXGI capture supports only regions fully contained within one display"
            )
        device_idx, output_idx, region = resolved
        camera_key = (device_idx, output_idx)
        camera = self._get_camera(device_idx, output_idx)
        self._last_used[camera_key] = self._clock()
        # dxcam's DXGI path polls AcquireNextFrame with a zero timeout. Right after
        # the AI indicator is hidden (or any click), the newest composed frame may
        # not have reached the duplication output yet, and new_frame_only=False then
        # returns dxcam's cached frame: the screen as it was before the change.
        # Flush composition and wait briefly for a new frame instead.
        _dwm_flush()
        full = None
        deadline = time.monotonic() + _NEW_FRAME_WAIT_SECONDS
        while True:
            full = camera.grab(copy=True, new_frame_only=True)
            if full is not None or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        if full is None:
            # Nothing changed since the last frame this camera returned.
            full = self._last_frames.get(camera_key)
        if full is None:
            full = camera.grab(copy=True, new_frame_only=False)
        if full is None:
            raise RuntimeError("DXGI capture returned no frame")
        self._last_frames[camera_key] = full
        self._last_used[camera_key] = self._clock()
        if region is None:
            return Image.fromarray(full)
        left, top, right, bottom = region
        return Image.fromarray(full[top:bottom, left:right])


class _PillowBackend(_ScreenshotBackend):
    """Capture via PIL *ImageGrab* (always available)."""

    name = "pillow"
    priority = 100

    def capture(self, capture_rect: uia.Rect | None) -> Image.Image:
        grab_kwargs: dict[str, object] = {"all_screens": True}
        if capture_rect is not None:
            grab_kwargs["bbox"] = (
                capture_rect.left,
                capture_rect.top,
                capture_rect.right,
                capture_rect.bottom,
            )
        try:
            screenshot = ImageGrab.grab(**grab_kwargs)
        except (OSError, RuntimeError, ValueError):
            if capture_rect is not None:
                logger.warning(
                    "Failed to capture selected region directly, "
                    "falling back to virtual screen crop"
                )
                # Fallback: grab full virtual screen then crop to the requested region.
                return _crop_screenshot(ImageGrab.grab(all_screens=True), capture_rect)
            logger.warning("Failed to capture virtual screen, using primary screen")
            screenshot = ImageGrab.grab()
        # Success path: ImageGrab.grab(bbox=...) already returned the exact region,
        # so no further cropping is needed.
        return screenshot


class _MssBackend(_ScreenshotBackend):
    """Capture via the *mss* library."""

    name = "mss"
    priority = 20

    def is_available(self, capture_rect: uia.Rect | None) -> bool:
        return mss is not None

    def capture(self, capture_rect: uia.Rect | None) -> Image.Image:
        if mss is None:
            raise RuntimeError("mss is not available")
        with mss.mss() as sct:
            if capture_rect is None:
                monitor = sct.monitors[0]
            else:
                monitor = {
                    "left": capture_rect.left,
                    "top": capture_rect.top,
                    "width": capture_rect.right - capture_rect.left,
                    "height": capture_rect.bottom - capture_rect.top,
                }
            raw = sct.grab(monitor)
            image = Image.frombytes("RGB", raw.size, raw.rgb)
        # mss.grab(monitor) already captures exactly the requested region,
        # so no further cropping is needed.
        return image


# ---------------------------------------------------------------------------
# Instance management
# ---------------------------------------------------------------------------

_backend_instances: dict[str, _ScreenshotBackend] = {}

#: Backends that have handed back an unusable frame in this process.
#: On VM/RDP desktops dxcam can initialize successfully and then return empty
#: frames from then on (issue #371). Retrying it on every call just reproduces
#: the same broken capture, so a backend caught doing this is skipped for the
#: rest of the process and the chain moves on to mss.
_degraded_backends: set[str] = set()


def _get_backend(name: str) -> _ScreenshotBackend:
    """Return a cached singleton instance for the given backend *name*."""
    if name not in _backend_instances:
        cls = _ScreenshotBackend.registry.get(name)
        if cls is None:
            raise ValueError(f"Unknown screenshot backend: {name!r}")
        _backend_instances[name] = cls()
    return _backend_instances[name]


def _is_usable_capture(image: Image.Image | None) -> bool:
    """Return True if a captured frame is structurally sound enough to encode.

    A backend that fails by raising is already handled by the chain below. The
    case this catches is the quiet one behind issue #371: on VM/RDP desktops
    dxcam can initialize successfully and then return frames carrying no pixel
    data, which travel all the way to the client as an undecodable image with no
    error anywhere in between.

    Only structure is checked, never content -- a legitimately black screen is a
    perfectly valid screenshot, and rejecting it would break locked and
    screensaver desktops.
    """
    if image is None:
        return False
    if image.width <= 0 or image.height <= 0:
        return False
    try:
        image.load()
    except (OSError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def capture(
    capture_rect: uia.Rect | None,
    backend: str | None = None,
) -> tuple[Image.Image, str]:
    """Capture a screenshot and return ``(image, backend_name_used)``."""
    selected = backend or get_screenshot_backend()

    # Build the candidate chain: all registered backends sorted by priority, or a single one.
    if selected == "auto":
        chain = sorted(_ScreenshotBackend.registry.values(), key=lambda c: c.priority)
    else:
        cls = _ScreenshotBackend.registry.get(selected)
        if cls is None:
            raise ValueError(f"Unknown screenshot backend: {selected!r}")
        chain = [cls]

    # Try each candidate: skip unavailable ones, catch failures and fall through.
    for backend_cls in chain:
        if backend_cls.name in _degraded_backends:
            continue
        inst = _get_backend(backend_cls.name)
        if not inst.is_available(capture_rect):
            continue
        try:
            image = inst.capture(capture_rect)
        except (OSError, RuntimeError, ValueError, IndexError, COMError):
            logger.warning(
                "Screenshot backend '%s' failed; trying next backend",
                inst.name,
                exc_info=selected != "auto",
            )
            continue

        # A backend can also fail silently, returning a frame with nothing in it.
        # Treat that exactly like a raised failure rather than shipping bytes the
        # client cannot decode.
        if not _is_usable_capture(image):
            _degraded_backends.add(inst.name)
            logger.warning(
                "Screenshot backend '%s' returned an unusable frame; disabling it "
                "for this process and trying the next backend",
                inst.name,
            )
            continue

        return image, inst.name

    # All candidates exhausted — pillow is always present as the last resort.
    return _get_backend("pillow").capture(capture_rect), "pillow"
