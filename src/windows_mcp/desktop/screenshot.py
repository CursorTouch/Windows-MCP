from dataclasses import dataclass
import logging
import os
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

    def capture(self, capture_rect: uia.Rect | None) -> Image.Image:
        resolved = self._resolve_region(capture_rect)
        if resolved is None:
            raise ValueError(
                "DXGI capture supports only regions fully contained within one display"
            )
        device_idx, output_idx, region = resolved
        camera = self._get_camera(device_idx, output_idx)
        frame = camera.grab(region=region, copy=True, new_frame_only=False)
        if frame is None:
            raise RuntimeError("DXGI capture returned no frame")
        return Image.fromarray(frame)


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


def _get_backend(name: str) -> _ScreenshotBackend:
    """Return a cached singleton instance for the given backend *name*."""
    if name not in _backend_instances:
        cls = _ScreenshotBackend.registry.get(name)
        if cls is None:
            raise ValueError(f"Unknown screenshot backend: {name!r}")
        _backend_instances[name] = cls()
    return _backend_instances[name]


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
        inst = _get_backend(backend_cls.name)
        if not inst.is_available(capture_rect):
            continue
        try:
            return inst.capture(capture_rect), inst.name
        except (OSError, RuntimeError, ValueError, IndexError):
            logger.warning(
                "Screenshot backend '%s' failed; trying next backend",
                inst.name,
                exc_info=selected != "auto",
            )

    # All candidates exhausted — pillow is always present as the last resort.
    return _get_backend("pillow").capture(capture_rect), "pillow"
