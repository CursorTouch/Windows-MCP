"""State coordination and hold cleanup, never on the Win32 hook pump."""

import queue
import time
from typing import Any, Callable


def refresh_deadline(owner: Any, now: float, fault=None) -> None:
    """Renew suppression only from a live, healthy coordinator proof."""
    fault = owner._fault_token if fault is None else fault

    def current():
        return (
            fault is owner._fault_token
            and not owner._emergency
            and not owner._stop.is_set()
            and time.monotonic() - now < 0.25
        )

    if not owner._lock.acquire(blocking=False):
        owner._fail_open()
        return
    try:
        if current() and owner._state == "ai" and owner._visual_armed and not owner._fast_takeover:
            owner._deadline = now + 1.0
            owner._suppress = True
            if not current():
                owner._suppress = False
                owner._deadline = 0.0
                owner.input_ledger.block_new()
        else:
            owner._suppress = False
    finally:
        owner._lock.release()


# Health, suppression refresh and the call lease need a short cadence only while
# the AI holds the desktop: suppression expires one second after its last refresh.
_AI_INTERVAL = 0.25
# A faulted coordinator re-probes health so it can recover after a rehook.
_FAULT_INTERVAL = 1.0
# Otherwise nothing is time-critical: input, state changes, faults and stop all
# wake the worker through its event, and this only refreshes desktop/indicator health.
_IDLE_INTERVAL = 2.0
_FIRST_WAIT = 0.01
_PUMP_PING_SECONDS = 1.0


def _health_interval(owner: Any) -> float:
    if owner._state == "ai" or owner._suppress:
        return _AI_INTERVAL
    if owner._emergency:
        return _FAULT_INTERVAL
    return _IDLE_INTERVAL


def _wait_seconds(owner: Any, health_done: float | None) -> float:
    """How long the worker may block before timed work is due in this state."""
    if health_done is None:
        return _FIRST_WAIT
    now = time.monotonic()
    due = health_done + _health_interval(owner)
    if owner._state == "user" and not owner._pressed and not owner._mouse_down:
        # Leave "user" exactly when the idle countdown ends; held input defers it
        # until its release event (or stale-hold recovery) wakes the worker.
        idle_at = max(owner._last_user, owner._last_physical_event) + owner.idle_timeout_seconds
        if idle_at > now:
            due = min(due, idle_at)
    return max(due - now, 0.001)


def _pump_alive(owner: Any, now: float) -> bool:
    """A busy pump must beat; a parked pump must answer a ping within a second."""
    idle = owner._pump_idle  # Read before the beat: the pump beats, then clears idle.
    beat = owner._thread_beat
    if now - beat <= 1.0:
        return True
    if not idle:
        return False  # Stuck in a callback or rotation rather than parked.
    ping = owner._pump_ping_at
    if ping <= beat:  # No ping outstanding: send one and judge it next time.
        owner._pump_ping_at = now
        owner._wake_pump()
        return True
    return now - ping <= _PUMP_PING_SECONDS


def run_control_worker(owner: Any, interactive_desktop: Callable[[], bool]) -> None:
    """Reuse the watchdog for ordered events; throttle expensive health probes."""
    health_done: float | None = None
    while True:
        owner._release_requested.wait(_wait_seconds(owner, health_done))
        owner._release_requested.clear()
        if owner._stop.is_set():
            return
        try:
            # Fault cleanup takes precedence over queued work and slow UI probes.
            if owner._emergency:
                owner._mark_unavailable()
            for _ in range(128):
                if owner._stop.is_set():
                    return
                try:
                    event = owner._events.get_nowait()
                except queue.Empty:
                    break
                owner._handle(event)
            else:
                # Batch cap hit: come straight back for the rest instead of
                # leaving it parked for a whole idle interval.
                owner._release_requested.set()
            owner._handle(("tick",))
            fault = owner._fault_token
            can_recover = owner._rehook_completed is fault and not owner._rotating
            if owner._emergency and not can_recover:
                continue
            if health_done is not None and time.monotonic() < health_done + _health_interval(owner):
                continue
            try:
                healthy = (
                    owner._health_probe is None or owner._health_probe()
                ) and interactive_desktop()
            finally:
                health_done = time.monotonic()
            now = time.monotonic()
            if (
                not healthy
                or not owner._thread
                or not owner._thread.is_alive()
                or not _pump_alive(owner, now)
            ):
                owner._fail_open()
                owner._mark_unavailable()
                continue
            if owner._emergency and can_recover:
                owner._recover_after_rehook(fault, healthy=True, sampled_at=now)
            if not owner._emergency:
                refresh_deadline(owner, now, fault)
        except Exception:
            owner._fail_open()
            owner._mark_unavailable()
