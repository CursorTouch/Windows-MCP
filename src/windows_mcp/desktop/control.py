"""Local desktop ownership and physical input interception.

The low level hooks never wait for the coordinator.  A stale control heartbeat
or a failed event queue releases physical input in the hook itself.
"""

from __future__ import annotations

import ctypes
import math
import queue
import threading
import time
from typing import Callable
from windows_mcp.desktop.control_context import current_token, current_steps, get_step_count
from windows_mcp.desktop.control_ledger import InputLedger
from windows_mcp.desktop import control_hooks
from windows_mcp.desktop.control_win32 import (
    _MouseHookData as _MouseHookData,  # Keep hook test helpers on this module.
    _KeyHookData as _KeyHookData,
    _user32,
    _key,
    _interactive_desktop,
    run_input_monitor,
)


class ControlBlocked(RuntimeError):
    def __init__(self, code: str, status: dict):
        self.code = code
        self.status = status
        super().__init__(f"{code}: {status['state']}")


class ControlCoordinator:
    def __init__(self, mouse_takeover_units: int = 120, mouse_takeover_pixels: int = 120):
        self.mouse_takeover_units = mouse_takeover_units
        self.mouse_takeover_pixels = mouse_takeover_pixels
        self._lock = threading.Lock()
        self._state = "unavailable"
        self._generation = 0
        self._active_calls = 0
        self._lease_until = 0.0
        self._last_user = 0.0
        self._last_physical_event = 0.0  # Written before hook events enter the queue.
        self._last_move = 0.0
        self._point_origin: tuple[int, int] | None = None
        self._raw_origin: dict[int, tuple[int, int]] = {}
        self._raw_device: int | None = None
        self._listeners: list[Callable[[dict], None]] = []
        self.input_ledger = InputLedger()
        self._health_probe: Callable[[], bool] | None = None
        self._begin_notification = threading.local()
        self._visual_armed = False
        self._visual_paused = False
        self._visual_event_stamp = 0.0
        self._events: queue.Queue[tuple] = queue.Queue(maxsize=4096)
        self._stop = threading.Event()
        self._release_requested = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None
        self._startup_error: Exception | None = None
        self._thread_beat = 0.0
        self._suppress = False  # Atomic snapshot read by the hook callbacks.
        self._deadline = 0.0
        self._emergency = False
        self._fast_takeover = False
        self._fast_pending = False
        self._rotating = False
        self._pressed: set[int] = set()
        self._mouse_down: set[int] = set()
        self._delivered_keys: set[int] = set()
        self._delivered_mouse: set[int] = set()
        self._quarantine: set[int] = set()
        self._mouse_hook = None
        self._key_hook = None
        self._mouse_callback = None
        self._key_callback = None
        self._window_callback = None
        self._rotate = False

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def set_health_probe(self, probe: Callable[[], bool]) -> None:
        """Require the visual indicator to stay alive during AI control."""
        self._health_probe = probe

    def visible_ack_required(self) -> bool:
        """Only a new MCP lease must wait for its visual acknowledgement."""
        return bool(getattr(self._begin_notification, "active", False))

    def arm_visible(self, generation: int) -> None:
        """Suppress physical input only after the AI indicator is visible."""
        with self._lock:
            if self._state != "ai" or self._generation != generation or self._visual_paused:
                return
            if (
                self._last_physical_event != self._visual_event_stamp
                or self._pressed
                or self._mouse_down
                or self._fast_takeover
                or self._fast_pending
                or self._emergency
            ):
                return
            self.input_ledger.enable()
            self._visual_armed = True
            self._deadline = time.monotonic() + 1.0
            self._suppress = True
            if self._last_physical_event != self._visual_event_stamp:
                self._suppress = False
                self._visual_armed = False
                self.input_ledger.block_new()

    def pause_for_capture(self) -> int | None:
        """Release physical input before an AI indicator is hidden for capture."""
        with self._lock:
            if self._state != "ai" or not self._visual_armed:
                return None
            self._suppress = False
            self._visual_armed = False
            self._visual_paused = True
            self._visual_event_stamp = self._last_physical_event
            self.input_ledger.block_new()
            return self._generation

    def resume_after_capture(self, generation: int | None, *, restored: bool) -> None:
        """Re-arm only after the indicator is restored and no user event occurred."""
        if generation is None:
            return
        if not restored or (self._health_probe is not None and not self._health_probe()):
            self._fail_open()
            return
        with self._lock:
            if generation == self._generation:
                self._visual_paused = False
        self.arm_visible(generation)

    def _snapshot_locked(self, now: float) -> dict:
        state = (
            "user"
            if self._fast_takeover
            else "takeover_pending"
            if self._fast_pending and self._state == "ai"
            else self._state
        )
        wait = (
            max(0.0, max(self._last_user, self._last_physical_event) + 10.0 - now)
            if state == "user"
            else 0.0
        )
        return {"state": state, "generation": self._generation, "wait_seconds": wait}

    def status(self) -> dict:
        if self._emergency:
            self._mark_unavailable()
        now = time.monotonic()
        with self._lock:
            changed = self._set_locked("unavailable") if self._emergency else self._tick_locked(now)
            result = self._snapshot_locked(now)
        if changed:
            self._notify(result)
        return result

    def _notify(self, result: dict) -> None:
        if result["state"] != "ai":
            # Serialize hold cleanup against a new lease, before any listener
            # can wait on a visual effect or an MCP transport.
            with self._lock:
                if result["generation"] != self._generation:
                    return
                try:
                    self.input_ledger.release_all()
                except Exception:
                    self._fail_open()
                    self._set_locked("unavailable")
                    result = self._snapshot_locked(time.monotonic())
        for listener in tuple(self._listeners):
            try:
                listener(result)
            except Exception:
                pass  # A disconnected MCP session must not hold desktop control.

    def _set_locked(self, state: str) -> bool:
        if state == self._state:
            return False
        self._state = state
        if state != "ai":
            self.input_ledger.block_new()
        self._generation += 1
        self._visual_armed = state == "ai" and self._health_probe is None
        self._visual_paused = False
        self._visual_event_stamp = self._last_physical_event
        self._point_origin = None
        self._raw_origin.clear()
        self._raw_device = None
        if state != "takeover_pending":
            self._fast_pending = False
        self._suppress = self._visual_armed and not self._emergency
        self._deadline = time.monotonic() + 1.0 if self._suppress else 0.0
        return True

    def _tick_locked(self, now: float) -> bool:
        if self._state == "ready" and now - self._last_physical_event < 10.0:
            self._last_user = self._last_physical_event
            return self._set_locked("user")
        if (
            self._state == "user"
            and not self._pressed
            and not self._mouse_down
            and now - max(self._last_user, self._last_physical_event) >= 10.0
        ):
            return self._set_locked("ready")
        if (
            self._state == "takeover_pending"
            and not self._mouse_down
            and now - self._last_move >= 0.3
        ):
            return self._set_locked(
                "ai" if self._active_calls or now < self._lease_until else "ready"
            )
        if self._state == "ai" and not self._active_calls and now >= self._lease_until:
            if now - self._last_physical_event < 10.0:
                self._last_user = self._last_physical_event
                return self._set_locked("user")
            return self._set_locked("ready")
        return False

    def begin_call(self, name: str) -> int:
        if self._emergency:
            self._mark_unavailable()
        now = time.monotonic()
        with self._lock:
            changed = self._set_locked("unavailable") if self._emergency else self._tick_locked(now)
            if self._pressed or self._mouse_down:
                raise ControlBlocked("PHYSICAL_INPUT_HELD", self._snapshot_locked(now))
            if (
                self._emergency
                or self._fast_takeover
                or self._fast_pending
                or self._rotating
                or self._visual_paused
                or self._state not in ("ready", "ai")
            ):
                status = self._snapshot_locked(now)
                code = {"user": "USER_CONTROL", "takeover_pending": "TAKEOVER_PENDING"}.get(
                    status["state"], "CONTROL_UNAVAILABLE"
                )
                raise ControlBlocked(code, status)
            try:
                self.input_ledger.enable()  # Any failed release keeps the lease closed.
            except RuntimeError:
                self._fail_open()
                self._set_locked("unavailable")
                raise ControlBlocked("CONTROL_UNAVAILABLE", self._snapshot_locked(now)) from None
            changed = self._set_locked("ai") or changed
            self._active_calls += 1
            token = self._generation
            result = self._snapshot_locked(now)
        if changed:
            self._begin_notification.active = True
            try:
                self._notify(result)
            finally:
                self._begin_notification.active = False
        return token

    def checkpoint(self, token: int) -> None:
        with self._lock:
            if (
                self._state != "ai"
                or self._generation != token
                or self._fast_takeover
                or self._fast_pending
                or self._rotating
                or self._emergency
                or not self._suppress
            ):
                status = self._snapshot_locked(time.monotonic())
                status["executed_steps"] = get_step_count()
                raise ControlBlocked("CONTROL_PREEMPTED", status)

    def checkpoint_current(self) -> None:
        """Validate each desktop input step inside a guarded MCP call."""
        token = current_token.get()
        if token is not None:
            self.checkpoint(token)

    def record_step_current(self) -> None:
        counter = current_steps.get()
        if current_token.get() is not None and counter is not None:
            counter.value += 1

    def physical_key_down(self, vk: int) -> bool:
        # A swallowed physical down did not reach the app, so the AI must
        # release its own injected down when its tool is preempted. Hook
        # callbacks mutate this set concurrently: use atomic membership checks
        # rather than iterating it while resolving left/right modifiers.
        normalized = _key(vk)
        aliases = {
            0x10: (0x10, 0xA0, 0xA1),
            0x11: (0x11, 0xA2, 0xA3),
            0x12: (0x12, 0xA4, 0xA5),
        }
        return any(key in self._delivered_keys for key in aliases.get(normalized, (vk,)))

    def physical_mouse_down(self, button: str) -> bool:
        return {"left": 1, "right": 2, "middle": 3}[button] in self._delivered_mouse

    def end_call(self, token: int) -> None:
        with self._lock:
            self._active_calls = max(0, self._active_calls - 1)
            if self._state in ("ai", "takeover_pending") and not self._active_calls:
                self._lease_until = time.monotonic() + 15.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            if self._stop.is_set():
                raise RuntimeError("Previous physical input monitor has not stopped")
            return
        self._stop.clear()
        self._release_requested.clear()
        self._ready.clear()
        self._startup_error = None
        if not _interactive_desktop():
            raise RuntimeError("No active interactive desktop for physical input control")
        self.input_ledger.release_all()
        self.input_ledger.enable()
        self._emergency = False
        self._fast_takeover = False
        self._fast_pending = False
        self._rotating = False
        self._thread = threading.Thread(target=self._run, name="windows-mcp-input", daemon=True)
        self._thread.start()
        if not self._ready.wait(3.0) or self._startup_error:
            self.stop()
            raise RuntimeError("Physical input monitor could not start") from self._startup_error
        self._watchdog = threading.Thread(
            target=self._watch, name="windows-mcp-input-watch", daemon=True
        )
        self._watchdog.start()
        with self._lock:
            now = time.monotonic()
            # A new server must not impose a cooldown without physical input.
            recent_input = self._last_physical_event > 0 and now - self._last_physical_event < 10.0
            if recent_input:
                self._last_user = self._last_physical_event
            self._set_locked("user" if recent_input else "ready")
            result = self._snapshot_locked(now)
        self._notify(result)

    def stop(self) -> None:
        self._suppress = False
        self._deadline = 0.0
        self.input_ledger.block_new()
        self._stop.set()
        self._release_requested.set()  # Wake the watchdog before joining it.
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._watchdog:
            self._watchdog.join(timeout=1.0)
        with self._lock:
            changed = self._set_locked("unavailable")
            result = self._snapshot_locked(time.monotonic())
        try:
            self.input_ledger.release_all()
        finally:
            if changed:
                self._notify(result)
        if (self._thread and self._thread.is_alive()) or (
            self._watchdog and self._watchdog.is_alive()
        ):
            raise RuntimeError("Physical input monitor did not stop cleanly")
        self._events = queue.Queue(maxsize=4096)
        self._pressed.clear()
        self._mouse_down.clear()
        self._delivered_keys.clear()
        self._delivered_mouse.clear()
        self._quarantine.clear()
        self._thread = self._watchdog = None

    def _fail_open(self) -> None:
        self._suppress = False
        self._deadline = 0.0
        self.input_ledger.block_new()  # Atomic flag; hooks must never wait for AI injection.
        self._emergency = True
        self._rotate = True
        # Hook callbacks must not wait on SendInput or the ledger lock. Wake
        # the existing watchdog to clear AI holds immediately off-hook.
        self._release_requested.set()

    def _mark_unavailable(self) -> None:
        # Clear injected holds before any visual or MCP listener can delay us.
        self.input_ledger.block_new()
        try:
            self.input_ledger.release_all()
        except Exception:
            pass  # Keep failed holds for the next watchdog/recovery attempt.
        if not self._lock.acquire(blocking=False):
            return
        try:
            changed = self._set_locked("unavailable")
            result = self._snapshot_locked(time.monotonic())
        finally:
            self._lock.release()
        if changed:
            self._notify(result)

    def _recover_after_rehook(self) -> None:
        """Reopen the lease only after hooks, display and held inputs are safe."""
        if not self._emergency or self._stop.is_set():
            return
        try:
            self.input_ledger.release_all()
            if self.input_ledger.pending():
                return
            if (
                self._health_probe is not None and not self._health_probe()
            ) or not _interactive_desktop():
                return
            with self._lock:
                self.input_ledger.enable()
                self._emergency = False
                self._fast_takeover = False
                self._fast_pending = False
                self._last_user = time.monotonic()
                changed = self._set_locked("user")
                result = self._snapshot_locked(self._last_user)
        except Exception:
            return  # A failed release or health check keeps control unavailable.
        if changed:
            self._notify(result)

    def _queue(self, event: tuple) -> None:
        try:
            self._events.put_nowait(event)
        except queue.Full:
            self._fail_open()

    def _physical_mouse(self, code, wparam, lparam):
        return control_hooks.physical_mouse(self, code, wparam, lparam)

    def _physical_key(self, code, wparam, lparam):
        return control_hooks.physical_key(self, code, wparam, lparam)

    def _raw_input(self, lparam) -> None:
        control_hooks.raw_input(self, lparam)

    def _handle(self, event: tuple) -> None:
        now = time.monotonic()
        with self._lock:
            if self._emergency:
                changed = self._set_locked("unavailable")
            else:
                changed = self._tick_locked(now)
                kind = event[0]
                if (
                    kind in ("key", "point", "raw")
                    and self._state == "ai"
                    and not self._visual_armed
                ):
                    # Physical input was delivered while the indicator was not armed.
                    self._last_user = now
                    changed = self._set_locked("user") or changed
                if kind == "hotkey" and self._state in ("ai", "takeover_pending"):
                    self._last_user = now
                    changed = self._set_locked("user") or changed
                    self._fast_takeover = False
                elif kind in ("key", "point", "raw") and self._state in ("ready", "user"):
                    self._last_user = now
                    changed = self._set_locked("user") or changed
                elif kind == "point" and self._state in ("ai", "takeover_pending"):
                    if event[3] == 0x200:  # WM_MOUSEMOVE
                        changed = self._candidate_locked(now) or changed
                        if self._point_origin is None:
                            self._point_origin = (event[1], event[2])
                        ox, oy = self._point_origin
                        if math.hypot(event[1] - ox, event[2] - oy) >= self.mouse_takeover_pixels:
                            self._last_user = now
                            changed = self._set_locked("user") or changed
                elif kind == "raw" and self._state in ("ai", "takeover_pending"):
                    changed = self._candidate_locked(now) or changed
                    device, dx, dy = event[1:]
                    if self._raw_device is not None and device != self._raw_device:
                        self._raw_origin.clear()  # A new device starts a new segment.
                    self._raw_device = device
                    x, y = self._raw_origin.get(device, (0, 0))
                    x, y = x + dx, y + dy
                    self._raw_origin[device] = (x, y)
                    if math.hypot(x, y) >= self.mouse_takeover_units:
                        self._last_user = now
                        changed = self._set_locked("user") or changed
            result = self._snapshot_locked(now)
        if changed:
            self._notify(result)

    def _candidate_locked(self, now: float) -> bool:
        if self._state == "ai":
            changed = self._set_locked("takeover_pending")
        else:
            changed = False
        self._last_move = now
        return changed

    def _install_hooks(self, instance) -> None:
        mouse = _user32.SetWindowsHookExW(14, self._mouse_callback, instance, 0)
        key = _user32.SetWindowsHookExW(13, self._key_callback, instance, 0)
        if not mouse or not key:
            if mouse:
                _user32.UnhookWindowsHookEx(mouse)
            if key:
                _user32.UnhookWindowsHookEx(key)
            raise ctypes.WinError(ctypes.get_last_error())
        old_mouse, old_key = self._mouse_hook, self._key_hook
        self._mouse_hook, self._key_hook = mouse, key
        for old in (old_mouse, old_key):
            if old:
                _user32.UnhookWindowsHookEx(old)

    def _run(self) -> None:
        # Keep the Win32 message pump alongside its raw-input structures.
        run_input_monitor(self)

    def _watch(self) -> None:
        while True:
            self._release_requested.wait(0.25)
            self._release_requested.clear()
            if self._stop.is_set():
                return
            # A hook fault wakes this thread specifically to release AI-held
            # input. Do that before potentially slow display/WTS probes.
            if self._emergency:
                self._mark_unavailable()
                continue
            now = time.monotonic()
            try:
                indicator_ok = self._health_probe is None or self._health_probe()
                desktop_ok = _interactive_desktop()
            except Exception:
                indicator_ok = False
                desktop_ok = False
            # The watchdog cannot renew suppression without a live coordinator
            # proof: a wedged lock, stopped message loop or emergency fails open.
            if (
                not self._thread
                or not self._thread.is_alive()
                or now - self._thread_beat > 1.0
                or not indicator_ok
                or not desktop_ok
            ):
                self._fail_open()
                self._mark_unavailable()
                continue
            if not self._lock.acquire(blocking=False):
                self._fail_open()
                continue
            try:
                if self._state == "ai" and self._visual_armed and not self._fast_takeover:
                    self._deadline = now + 1.0
                    self._suppress = True
                else:
                    self._suppress = False
            finally:
                self._lock.release()


_controller = ControlCoordinator()


def get_controller() -> ControlCoordinator:
    return _controller
