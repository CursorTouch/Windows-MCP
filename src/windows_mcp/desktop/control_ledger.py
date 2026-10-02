"""Track AI-held keys and mouse buttons until each injected down is cleared."""

from __future__ import annotations

import threading
from collections.abc import Callable

from fastmcp.exceptions import ToolError


class InputUnavailable(ToolError):
    """A physical takeover or fault blocked a new injected key/button down."""


class InputLedger:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._holds: dict[str, tuple[Callable[[], None], Callable[[], bool]]] = {}
        self._blocked = False

    def block_new(self) -> None:
        self._blocked = True

    def enable(self) -> None:
        with self._lock:
            if self._holds:
                raise RuntimeError("AI input remains held")
            self._blocked = False

    def press(
        self,
        name: str,
        send_down: Callable[[], None],
        send_up: Callable[[], None],
        physical_down_delivered: Callable[[], bool],
    ) -> None:
        """Record before injection: a send routine may inject and then raise."""
        with self._lock:
            if self._blocked:
                raise InputUnavailable("AI input is unavailable")
            if name in self._holds:
                raise RuntimeError(f"AI input already held: {name}")
            self._holds[name] = (send_up, physical_down_delivered)
            try:
                # Keep the lock through injection so a watchdog cannot send
                # up before the matching down has actually reached Windows.
                send_down()
            except BaseException:
                try:
                    self.release(name)
                except Exception:
                    pass  # Keep the entry for shutdown's second attempt.
                raise

    def release(self, name: str) -> None:
        with self._lock:
            entry = self._holds.get(name)
            if entry is None:
                return
            send_up, physical_down_delivered = entry
            # If a physical down reached the app after takeover, its own up
            # will clear the state. Injecting another up would steal the key.
            if not physical_down_delivered():
                send_up()
            self._holds.pop(name, None)

    def release_all(self) -> None:
        with self._lock:
            names = tuple(self._holds)
        first_error: Exception | None = None
        for name in names:
            try:
                self.release(name)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def pending(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._holds)
