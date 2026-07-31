from dataclasses import dataclass
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from _ctypes import COMError
from pathlib import Path

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

_ISOLATION_LOCK = threading.Lock()
_isolation_failures = 0
_circuit_open_until = 0.0
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _isolation_enabled() -> bool:
    return _env_flag("WINDOWS_MCP_SCREENSHOT_ISOLATION", False)


def _is_worker_process() -> bool:
    return _env_flag("WINDOWS_MCP_SCREENSHOT_WORKER", False)


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _format_return_code(return_code: int) -> str:
    unsigned = return_code & 0xFFFFFFFF
    return f"{return_code} (0x{unsigned:08X})"


def _circuit_remaining_seconds() -> float:
    return max(0.0, _circuit_open_until - time.monotonic())


def _record_isolation_success() -> None:
    global _isolation_failures, _circuit_open_until
    _isolation_failures = 0
    _circuit_open_until = 0.0


def _record_isolation_failure() -> None:
    global _isolation_failures, _circuit_open_until
    _isolation_failures += 1
    threshold = _bounded_int("WINDOWS_MCP_SCREENSHOT_FAILURE_THRESHOLD", 2, 1, 10)
    if _isolation_failures >= threshold:
        cooldown = _bounded_float("WINDOWS_MCP_SCREENSHOT_COOLDOWN_SECONDS", 120.0, 5.0, 3600.0)
        _circuit_open_until = time.monotonic() + cooldown


def _capture_isolated(
    capture_rect: uia.Rect | None,
    selected_backend: str,
) -> tuple[Image.Image, str]:
    """Capture in a disposable child process so native faults cannot kill MCP."""
    with _ISOLATION_LOCK:
        remaining = _circuit_remaining_seconds()
        if remaining > 0:
            raise RuntimeError(
                f"Screenshot circuit breaker is open for {remaining:.1f}s after repeated failures"
            )

        timeout = _bounded_float("WINDOWS_MCP_SCREENSHOT_TIMEOUT_SECONDS", 15.0, 1.0, 120.0)
        with tempfile.TemporaryDirectory(prefix="windows-mcp-screenshot-") as temp_dir:
            output_path = Path(temp_dir) / "capture.png"
            command = [
                sys.executable,
                "-m",
                "windows_mcp.desktop.screenshot_worker",
                "--backend",
                selected_backend,
                "--output",
                str(output_path),
            ]
            if capture_rect is not None:
                command.extend(
                    [
                        "--rect",
                        f"{capture_rect.left},{capture_rect.top},{capture_rect.right},{capture_rect.bottom}",
                    ]
                )
            env = os.environ.copy()
            env.update(
                {
                    "WINDOWS_MCP_SCREENSHOT_WORKER": "1",
                    "WINDOWS_MCP_SCREENSHOT_ISOLATION": "0",
                    "WINDOWS_MCP_DISABLE_FLASH": "1",
                    "ANONYMIZED_TELEMETRY": "false",
                    "NO_COLOR": "1",
                    "PYTHONIOENCODING": "utf-8",
                }
            )
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    check=False,
                    env=env,
                    creationflags=_CREATE_NO_WINDOW,
                )
            except subprocess.TimeoutExpired as exc:
                _record_isolation_failure()
                raise RuntimeError(f"Screenshot child timed out after {timeout:.1f}s") from exc

            if completed.returncode != 0:
                _record_isolation_failure()
                stderr = (completed.stderr or "").strip()[-2000:]
                raise RuntimeError(
                    "Screenshot child exited with code "
                    f"{_format_return_code(completed.returncode)}; stderr={stderr or '<empty>'}"
                )
            if not output_path.is_file() or output_path.stat().st_size == 0:
                _record_isolation_failure()
                raise RuntimeError("Screenshot child completed without a valid image")

            try:
                payload_line = next(
                    line for line in reversed((completed.stdout or "").splitlines()) if line.strip()
                )
                payload = json.loads(payload_line)
                used_backend = str(payload.get("backend") or selected_backend)
            except (StopIteration, json.JSONDecodeError, TypeError, ValueError):
                used_backend = selected_backend

            with Image.open(output_path) as source:
                source.load()
                image = source.copy()
            _record_isolation_success()
            return image, used_backend


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


def _capture_in_process(
    capture_rect: uia.Rect | None,
    backend: str | None = None,
) -> tuple[Image.Image, str]:
    """Capture in the current process. Used only by tests and the isolated worker."""
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

def capture(
    capture_rect: uia.Rect | None,
    backend: str | None = None,
) -> tuple[Image.Image, str]:
    """Capture a screenshot, isolated by default when enabled for stdio."""
    selected = backend or get_screenshot_backend()
    if _env_flag("WINDOWS_MCP_SCREENSHOT_QUARANTINED", False) and not _is_worker_process():
        raise RuntimeError(
            "Screenshot is quarantined: isolated graphics workers are denied desktop capture "
            "on this Windows session. Other MCP tools remain available."
        )
    if _isolation_enabled() and not _is_worker_process():
        return _capture_isolated(capture_rect, selected)
    return _capture_in_process(capture_rect, selected)
