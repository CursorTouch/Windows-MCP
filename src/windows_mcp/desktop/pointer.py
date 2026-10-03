"""Stateful mouse pointer control with bounded automatic release."""

from collections.abc import Callable
import logging
import math
from threading import RLock, Timer
from time import sleep
from typing import Literal

import windows_mcp.uia as uia
from windows_mcp.desktop.control import get_controller


MouseButton = Literal["left", "right", "middle"]
DEFAULT_POINTER_TIMEOUT = 30.0
MAX_POINTER_TIMEOUT = 120.0
MAX_POINTER_MOVE_DURATION = 10.0
RELEASE_RETRY_DELAY = 1.0
MAX_RELEASE_ATTEMPTS = 3

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
        *,
        control=None,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self._lock = RLock()
        self._button: MouseButton | None = None
        self._receipt: object | None = None
        self._timer: Timer | None = None
        self._generation = 0
        self._timer_factory = timer_factory
        self._control = control if control is not None else get_controller()
        self._sleep = sleeper

    @property
    def held_button(self) -> MouseButton | None:
        """Return the button currently held by this controller."""
        with self._lock:
            self._owns_locked()
            return self._button

    def _owns_locked(self) -> bool:
        """Discard a gesture already released by takeover or the watchdog."""
        if self._button is None:
            return False
        receipt = self._receipt
        if receipt is not None and self._control.input_ledger.owns(
            f"mouse:{self._button}", "Pointer", receipt
        ):
            return True
        self._clear_locked(cancel_timer=True)
        return False

    def _owns_receipt_locked(self, button: MouseButton, receipt: object) -> bool:
        """A delayed move must never attach itself to a newer gesture."""
        return self._button == button and self._receipt is receipt and self._owns_locked()

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
        self._receipt = None
        self._generation += 1
        if cancel_timer and timer is not None:
            timer.cancel()

    def _release_owned_locked(self) -> None:
        """Release only this controller's button, not unrelated physical input."""
        button = self._button
        receipt = self._receipt
        if button is not None and receipt is not None:
            # A failed Win32 release may leave the button down. Keep ownership
            # and the timer so cancel or a later timeout can retry safely.
            self._control.input_ledger.release(f"mouse:{button}", owner="Pointer", receipt=receipt)
        self._clear_locked(cancel_timer=True)

    def _timeout_release(self, generation: int, attempt: int = 1) -> None:
        with self._lock:
            if self._button is None or self._generation != generation:
                return
            try:
                self._release_owned_locked()
            except Exception:
                logger.exception("Automatic pointer release failed")
                if attempt >= MAX_RELEASE_ATTEMPTS:
                    # Keep the ledger entry for the watchdog and stop physical
                    # interception rather than holding the desktop forever.
                    self._control._fail_open()
                    return
                try:
                    timer = self._timer_factory(
                        RELEASE_RETRY_DELAY,
                        lambda: self._timeout_release(generation, attempt + 1),
                    )
                    timer.daemon = True
                    self._timer = timer
                    timer.start()
                except Exception:
                    logger.exception("Failed to schedule pointer release retry")
                    # A missing retry timer cannot justify keeping physical
                    # input intercepted while this AI hold remains unresolved.
                    self._control._fail_open()

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
            self._control.checkpoint_current()
            self._owns_locked()
            if self._button is not None:
                raise RuntimeError(
                    f"Cannot press {normalized_button}; {self._button} mouse button is already held"
                )
            self._button = normalized_button
            receipt = object()
            self._receipt = receipt
            self._generation += 1
            generation = self._generation
            try:
                # Arm recovery before injecting input: even an ambiguous press
                # failure retains a timed path to release the tracked button.
                timer = self._timer_factory(
                    normalized_timeout,
                    lambda: self._timeout_release(generation),
                )
                timer.daemon = True
                self._timer = timer
                timer.start()
            except BaseException:
                self._clear_locked(cancel_timer=True)
                raise

            try:
                self._control.input_ledger.press(
                    f"mouse:{normalized_button}",
                    lambda: self._press(normalized_button, x, y),
                    lambda: self._release(normalized_button),
                    lambda: self._control.physical_mouse_down(normalized_button),
                    owner="Pointer",
                    receipt=receipt,
                )
            except BaseException:
                if not self._control.input_ledger.owns(
                    f"mouse:{normalized_button}", "Pointer", receipt
                ):
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
            if not self._owns_locked():
                raise RuntimeError("Cannot move pointer because no mouse button is held")
            button, receipt = self._button, self._receipt
            assert button is not None and receipt is not None
        try:
            if normalized_duration is None:
                with self._lock:
                    self._control.checkpoint_current()
                    if not self._owns_receipt_locked(button, receipt):
                        raise RuntimeError("Pointer gesture was released")
                    # An unsmoothed move is one bounded input step.
                    uia.SetCursorPos(x, y)
                    self._control.record_step_current()
            else:
                start_x, start_y = uia.GetCursorPos()
                steps = max(1, math.ceil(normalized_duration / 0.02))
                for step in range(1, steps + 1):
                    with self._lock:
                        self._control.checkpoint_current()
                        if not self._owns_receipt_locked(button, receipt):
                            raise RuntimeError("Pointer gesture was released")
                    # Do not hold the Pointer lock during sleep: its timer must
                    # be able to release the button at the configured deadline.
                    self._sleep(normalized_duration / steps)
                    with self._lock:
                        self._control.checkpoint_current()
                        if not self._owns_receipt_locked(button, receipt):
                            raise RuntimeError("Pointer gesture was released")
                        uia.SetCursorPos(
                            start_x + (x - start_x) * step // steps,
                            start_y + (y - start_y) * step // steps,
                        )
                        self._control.record_step_current()
        except BaseException as move_error:
            with self._lock:
                if self._receipt is receipt:
                    try:
                        self._release_owned_locked()
                    except BaseException as release_error:
                        raise release_error from move_error
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
            if not self._owns_locked():
                raise RuntimeError("Cannot release pointer because no mouse button is held")
            if normalized_button is not None and normalized_button != self._button:
                raise RuntimeError(
                    f"Cannot release {normalized_button}; {self._button} mouse button is held"
                )
            held_button = self._button
            self._release_owned_locked()
            return {"action": "up", "button": held_button}

    def cancel(self) -> dict[str, object]:
        """Release the tracked button and clear state without touching other buttons."""
        with self._lock:
            self._owns_locked()
            held_button = self._button
            self._release_owned_locked()
            return {"action": "cancel", "button": held_button}

    def close(self) -> None:
        """Release tracked input during a normal server shutdown."""
        with self._lock:
            self._owns_locked()
            if self._button is None:
                self._clear_locked(cancel_timer=True)
                return
            self._release_owned_locked()
