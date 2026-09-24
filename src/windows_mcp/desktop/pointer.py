"""Stateful mouse pointer control with bounded automatic release."""

from collections.abc import Callable
import logging
import math
from threading import RLock, Timer
from typing import Literal

import windows_mcp.uia as uia


MouseButton = Literal["left", "right", "middle"]
DEFAULT_POINTER_TIMEOUT = 30.0
MAX_POINTER_TIMEOUT = 120.0
MAX_POINTER_MOVE_DURATION = 10.0

logger = logging.getLogger(__name__)


def normalize_pointer_button(value: object, *, allow_none: bool = False) -> MouseButton | None:
    """Validate a mouse button name."""
    if value is None and allow_none:
        return None
    if value not in {"left", "right", "middle"}:
        raise ValueError("button must be one of: left, right, middle")
    return value


def normalize_pointer_point(value: object, name: str = "loc") -> tuple[int, int]:
    """Validate a screen point without accepting booleans or fractional coordinates."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must be a list or tuple of exactly 2 integers [x, y]")
    x, y = value
    if any(isinstance(item, bool) or not isinstance(item, int) for item in (x, y)):
        raise ValueError(f"{name} must contain exactly 2 integers")
    return x, y


def normalize_pointer_duration(value: object | None) -> float | None:
    """Validate an optional bounded pointer movement duration."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("duration must be a finite number of seconds")
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("duration must be a finite number of seconds") from exc
    if not math.isfinite(duration):
        raise ValueError("duration must be a finite number of seconds")
    if duration < 0 or duration > MAX_POINTER_MOVE_DURATION:
        raise ValueError(f"duration must be between 0 and {MAX_POINTER_MOVE_DURATION:g} seconds")
    return duration


def normalize_pointer_timeout(value: object | None) -> float:
    """Validate the automatic mouse-button release timeout."""
    if value is None:
        return DEFAULT_POINTER_TIMEOUT
    if isinstance(value, bool):
        raise ValueError("timeout must be a finite number of seconds")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be a finite number of seconds") from exc
    if not math.isfinite(timeout):
        raise ValueError("timeout must be a finite number of seconds")
    if timeout <= 0 or timeout > MAX_POINTER_TIMEOUT:
        raise ValueError(
            f"timeout must be greater than 0 and at most {MAX_POINTER_TIMEOUT:g} seconds"
        )
    return timeout


class PointerController:
    """Serialize stateful mouse input and prevent indefinitely held buttons."""

    def __init__(
        self,
        timer_factory: Callable[[float, Callable[[], None]], Timer] = Timer,
    ) -> None:
        self._lock = RLock()
        self._button: MouseButton | None = None
        self._timer: Timer | None = None
        self._generation = 0
        self._timer_factory = timer_factory

    @property
    def held_button(self) -> MouseButton | None:
        """Return the button currently held by this controller."""
        with self._lock:
            return self._button

    @staticmethod
    def _press(button: MouseButton, x: int, y: int) -> None:
        if button == "left":
            uia.PressMouse(x, y, waitTime=0.05)
        elif button == "right":
            uia.RightPressMouse(x, y, waitTime=0.05)
        else:
            uia.MiddlePressMouse(x, y, waitTime=0.05)

    @staticmethod
    def _release(button: MouseButton) -> None:
        if button == "left":
            uia.ReleaseMouse(waitTime=0.05)
        elif button == "right":
            uia.RightReleaseMouse(waitTime=0.05)
        else:
            uia.MiddleReleaseMouse(waitTime=0.05)

    def _clear_locked(self, *, cancel_timer: bool) -> None:
        timer = self._timer
        self._timer = None
        self._button = None
        self._generation += 1
        if cancel_timer and timer is not None:
            timer.cancel()

    def _release_all_locked(self) -> None:
        first_error: Exception | None = None
        for button in ("left", "right", "middle"):
            try:
                self._release(button)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        self._clear_locked(cancel_timer=True)
        if first_error is not None:
            raise RuntimeError("Failed to release one or more mouse buttons") from first_error

    def _timeout_release(self, generation: int) -> None:
        with self._lock:
            if self._button is None or self._generation != generation:
                return
            try:
                self._release_all_locked()
            except Exception:
                logger.exception("Automatic pointer release failed")

    def down(
        self,
        loc: tuple[int, int] | list[int],
        button: MouseButton = "left",
        timeout: float | int | str | None = None,
    ) -> dict[str, object]:
        """Press one mouse button and schedule a bounded automatic release."""
        x, y = normalize_pointer_point(loc)
        normalized_button = normalize_pointer_button(button)
        normalized_timeout = normalize_pointer_timeout(timeout)

        with self._lock:
            if self._button is not None:
                raise RuntimeError(
                    f"Cannot press {normalized_button}; {self._button} mouse button is already held"
                )
            try:
                self._press(normalized_button, x, y)
            except BaseException as press_error:
                try:
                    self._release(normalized_button)
                except BaseException as release_error:
                    raise release_error from press_error
                raise

            self._button = normalized_button
            self._generation += 1
            generation = self._generation
            try:
                timer = self._timer_factory(
                    normalized_timeout,
                    lambda: self._timeout_release(generation),
                )
                timer.daemon = True
                self._timer = timer
                timer.start()
            except BaseException as timer_error:
                try:
                    self._release(normalized_button)
                except BaseException as release_error:
                    raise release_error from timer_error
                finally:
                    self._clear_locked(cancel_timer=True)
                raise

            return {
                "action": "down",
                "button": normalized_button,
                "loc": [x, y],
                "timeout": normalized_timeout,
            }

    def move(
        self,
        loc: tuple[int, int] | list[int],
        duration: float | int | str | None = None,
    ) -> dict[str, object]:
        """Move the pointer while the tracked mouse button remains held."""
        x, y = normalize_pointer_point(loc)
        normalized_duration = normalize_pointer_duration(duration)

        with self._lock:
            if self._button is None:
                raise RuntimeError("Cannot move pointer because no mouse button is held")
            button = self._button
            try:
                if normalized_duration is None:
                    uia.MoveTo(x, y, moveSpeed=10, waitTime=0.05)
                else:
                    uia.MoveToDuration(x, y, normalized_duration, waitTime=0.05)
            except BaseException as move_error:
                try:
                    self._release(button)
                except BaseException as release_error:
                    raise release_error from move_error
                else:
                    self._clear_locked(cancel_timer=True)
                raise

            return {
                "action": "move",
                "button": button,
                "loc": [x, y],
                "duration": normalized_duration,
            }

    def up(self, button: MouseButton | None = None) -> dict[str, object]:
        """Release the tracked mouse button, optionally asserting its identity."""
        normalized_button = normalize_pointer_button(button, allow_none=True)
        with self._lock:
            if self._button is None:
                raise RuntimeError("Cannot release pointer because no mouse button is held")
            if normalized_button is not None and normalized_button != self._button:
                raise RuntimeError(
                    f"Cannot release {normalized_button}; {self._button} mouse button is held"
                )
            held_button = self._button
            self._release(held_button)
            self._clear_locked(cancel_timer=True)
            return {"action": "up", "button": held_button}

    def cancel(self) -> dict[str, object]:
        """Best-effort release every supported mouse button and clear tracked state."""
        with self._lock:
            held_button = self._button
            self._release_all_locked()
            return {"action": "cancel", "button": held_button}

    def close(self) -> None:
        """Release tracked input during a normal server shutdown."""
        with self._lock:
            if self._button is None:
                self._clear_locked(cancel_timer=True)
                return
            self._release_all_locked()
