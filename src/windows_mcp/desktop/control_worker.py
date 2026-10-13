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


def run_control_worker(owner: Any, interactive_desktop: Callable[[], bool]) -> None:
    """Reuse the watchdog for ordered events; throttle expensive health probes."""
    next_health = 0.0
    while True:
        owner._release_requested.wait(0.01)
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
            owner._handle(("tick",))
            fault = owner._fault_token
            can_recover = owner._rehook_completed is fault and not owner._rotating
            if owner._emergency and not can_recover:
                continue
            if time.monotonic() < next_health:
                continue
            try:
                healthy = (
                    owner._health_probe is None or owner._health_probe()
                ) and interactive_desktop()
            finally:
                next_health = time.monotonic() + 0.25
            now = time.monotonic()
            if (
                not healthy
                or not owner._thread
                or not owner._thread.is_alive()
                or now - owner._thread_beat > 1.0
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
