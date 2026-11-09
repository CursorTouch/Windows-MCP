"""Best-effort recovery of missed physical key/button releases, off-hook."""

import time
from typing import Any, Callable

# Hook button IDs differ from VK codes for middle and X buttons.
_MOUSE_VK = {1: 0x01, 2: 0x02, 3: 0x04, 4: 0x05, 5: 0x06}
_SAMPLE_INTERVAL = 0.1
_RELEASE_SAMPLES = 5


class StaleInputRecovery:
    """Owned exclusively by one input-monitor lifetime, never by status callers."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self._signature = None
        self._count = 0
        self._next_sample = 0.0

    @staticmethod
    def _snapshot(owner: Any, now: float) -> tuple | None:
        # Called under owner._lock; physical callbacks run on our pump thread.
        # AI control never expires, so a missed physical up must also clear between AI
        # calls; begin_call keeps rejecting the hold (PHYSICAL_INPUT_HELD) until it does.
        idle_ai = owner._state in ("ai", "takeover_pending") and not owner._active_calls
        if (
            not owner.enabled
            or (owner._state not in ("user", "ready") and not idle_ai)
            or owner._emergency
            or owner._stop.is_set()
            or now - max(owner._last_user, owner._last_physical_event) < owner.idle_timeout_seconds
            or not (owner._pressed or owner._mouse_down)
        ):
            return None
        return (
            owner._generation,
            owner._last_user,
            owner._last_physical_event,
            frozenset(owner._pressed),
            frozenset(owner._mouse_down),
        )

    def sample(self, owner: Any, now: float, key_state: Callable[[int], int]) -> None:
        """Clear stale holds after five spaced all-up samples while idle outside AI."""
        # The hook pump must remain available to a sender holding this lock.
        if not owner._lock.acquire(blocking=False):
            self._reset()
            return
        try:
            signature = self._snapshot(owner, now)
        finally:
            owner._lock.release()
        if signature is None:
            self._reset()
            return
        if signature != self._signature:
            self._reset()
            self._signature = signature
        if now < self._next_sample:
            return
        keys, buttons = signature[-2:]
        # Read the full set before clearing anything. Zero (even an ambiguous
        # Win32 failure) deliberately counts as up; only the high bit means down.
        states = [key_state(vk) for vk in keys]
        states.extend(key_state(_MOUSE_VK[button]) for button in buttons)
        # Anchor spacing after the actual queries: lock contention or slow OS
        # calls must not consume the gap before the next confirmation.
        self._next_sample = time.monotonic() + _SAMPLE_INTERVAL
        if not owner._lock.acquire(blocking=False):
            self._reset()
            return
        try:
            if self._snapshot(owner, now) != signature:
                self._reset()  # Input/state changed while the Win32 queries ran.
                return
            if any(state & 0x8000 for state in states):
                self._count = 0
                return
            self._count += 1
            if self._count < _RELEASE_SAMPLES:
                return
            # No Win32 calls here: callbacks on this same thread cannot interleave
            # with cleanup. Do not leave stale delivery or swallowed-chord flags.
            owner._pressed.difference_update(keys)
            owner._mouse_down.difference_update(buttons)
            owner._delivered_keys.difference_update(keys)
            owner._delivered_mouse.difference_update(buttons)
            owner._quarantine.difference_update(keys)
            self._reset()
        finally:
            owner._lock.release()
