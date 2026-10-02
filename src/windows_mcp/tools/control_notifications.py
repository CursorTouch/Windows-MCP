"""Global MCP ownership gate and best-effort session state notifications."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger(__name__)


@dataclass
class _Session:
    session: Any
    loop: asyncio.AbstractEventLoop
    last_seen: float


class ControlNotifier:
    """Send state transitions to live MCP sessions without holding control locks."""

    def __init__(self, controller: Any, *, ttl: float = 60.0) -> None:
        self.controller = controller
        self.ttl = ttl
        self._sessions: dict[int, _Session] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reaper: asyncio.Task | None = None
        self._state = "unavailable"
        self._generation = -1
        self._active = False
        self._subscribed = False
        self.push_enabled = True
        self._pending: list[dict] = []
        self._flush_handle: asyncio.TimerHandle | None = None
        self._send_tasks: set[asyncio.Task] = set()

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._active = True
        initial = self.controller.status()
        self._state = initial["state"]
        self._generation = initial["generation"]
        if not self._subscribed:
            self.controller.subscribe(self._on_state)
            self._subscribed = True
        self._reaper = self._loop.create_task(self._reap())

    async def close(self) -> None:
        self._active = False
        if self._flush_handle:
            self._flush_handle.cancel()
            self._flush_handle = None
        self._pending.clear()
        if self._reaper:
            self._reaper.cancel()
            try:
                await self._reaper
            except asyncio.CancelledError:
                pass
            self._reaper = None
        tasks = tuple(self._send_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._send_tasks.clear()
        self._sessions.clear()
        self._loop = None

    def remember(self, context: MiddlewareContext) -> None:
        """Refresh only sessions seen in a real request; stateless sessions expire."""
        if not self._active or not self.push_enabled or context.fastmcp_context is None:
            return
        request_loop = asyncio.get_running_loop()
        if request_loop is not self._loop:
            # A session on another event loop cannot be sent or awaited safely
            # by this server's notifier; the next tool response remains the fallback.
            return
        try:
            session = context.fastmcp_context.session
        except RuntimeError:
            return
        self._sessions[id(session)] = _Session(session, request_loop, time.monotonic())

    async def _reap(self) -> None:
        while self._active:
            await asyncio.sleep(10)
            cutoff = time.monotonic() - self.ttl
            for key, value in tuple(self._sessions.items()):
                if value.last_seen < cutoff or value.loop.is_closed():
                    self._sessions.pop(key, None)

    def _on_state(self, status: dict) -> None:
        loop = self._loop
        if not self._active or loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._enqueue, status.copy())
        except RuntimeError:
            # Shutdown can close the event loop between the check and scheduling.
            logger.debug("Control event loop closed before notification dispatch")

    def _enqueue(self, status: dict) -> None:
        if not self._active:
            return
        self._pending.append(status)
        if self._flush_handle is None:
            # State callbacks can race after releasing the coordinator lock.
            # A short batch sorts by generation before notifying clients.
            self._flush_handle = asyncio.get_running_loop().call_later(0.02, self._flush)

    def _flush(self) -> None:
        self._flush_handle = None
        pending, self._pending = self._pending, []
        for status in sorted(pending, key=lambda item: item["generation"]):
            self._dispatch(status)

    def _dispatch(self, status: dict) -> None:
        if not self._active:
            return
        if status["generation"] <= self._generation:
            return  # Cross-thread delivery can reorder old state events.
        self._generation = status["generation"]
        previous, current = self._state, status["state"]
        self._state = current
        event = None
        if current == "user" and previous in ("ready", "ai", "takeover_pending"):
            event = "USER_CONTROL"
        elif current == "ready" and previous == "user":
            event = "AI_CONTROL_AVAILABLE"
        if event is None:
            return
        cutoff = time.monotonic() - self.ttl
        for key, value in tuple(self._sessions.items()):
            if value.last_seen < cutoff or value.loop.is_closed():
                self._sessions.pop(key, None)
                continue
            task = value.loop.create_task(self._send(key, value, event, status))
            self._send_tasks.add(task)
            task.add_done_callback(self._send_tasks.discard)

    async def _send(self, key: int, entry: _Session, event: str, status: dict) -> None:
        if not self._active:
            return
        try:
            await entry.session.send_log_message(
                level="notice",
                data={"event": event, "status": status},
                logger="windows-mcp.control",
            )
        except Exception:
            self._sessions.pop(key, None)
            logger.debug("MCP control notification failed; session removed", exc_info=True)


class ControlToolGate(Middleware):
    """All present and future MCP tools use the same ownership decision."""

    def __init__(self, controller: Any, notifier: ControlNotifier) -> None:
        self.controller = controller
        self.notifier = notifier
        self._call_lock = asyncio.Lock()

    @staticmethod
    def _blocked(exc: Exception) -> ToolError:
        return ToolError(
            json.dumps(
                {"code": exc.code, "status": exc.status},
                ensure_ascii=False,
            )
        )

    async def _begin_call(self, name: str) -> int:
        """Wait for the visible lease off-loop, retaining ownership on cancel."""
        worker = asyncio.create_task(asyncio.to_thread(self.controller.begin_call, name))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # The worker cannot be stopped safely mid-ack. Keep the gate lock
            # until it finishes, then balance a lease created after cancellation.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled():
                try:
                    token = worker.result()
                except Exception:
                    pass  # A failed begin_call never created a usable lease.
                else:
                    self.controller.end_call(token)
            raise

    async def on_call_tool(self, context: MiddlewareContext, call_next: Callable) -> Any:
        self.notifier.remember(context)
        name = context.message.name
        if name == "ControlStatus":
            return await call_next(context)
        # Waiting behind an already running external command must not hide a
        # user takeover. Poll ownership while waiting, then recheck under lock.
        from windows_mcp.desktop.control import ControlBlocked

        def reject_user_state() -> None:
            status = self.controller.status()
            if status["state"] not in ("ready", "ai"):
                raise self._blocked(
                    ControlBlocked(
                        {"user": "USER_CONTROL", "takeover_pending": "TAKEOVER_PENDING"}.get(
                            status["state"], "CONTROL_UNAVAILABLE"
                        ),
                        status,
                    )
                )

        reject_user_state()
        while True:
            try:
                await asyncio.wait_for(self._call_lock.acquire(), timeout=0.1)
                break
            except asyncio.TimeoutError:
                reject_user_state()
        try:
            try:
                token = await self._begin_call(name)
            except Exception as exc:
                from windows_mcp.desktop.control import ControlBlocked

                if not isinstance(exc, ControlBlocked):
                    raise
                raise self._blocked(exc) from exc
            from windows_mcp.desktop.control_context import (
                current_token,
                current_steps,
                StepCounter,
                get_step_count,
            )

            context_token = current_token.set(token)
            steps_token = current_steps.set(StepCounter())
            try:
                # A synchronous indicator failure can invalidate the lease
                # during begin_call's state notification.
                self.controller.checkpoint(token)
                return await call_next(context)
            except Exception as exc:
                from windows_mcp.desktop.control import ControlBlocked
                from windows_mcp.desktop.control_ledger import InputUnavailable

                if isinstance(exc, ControlBlocked):
                    raise self._blocked(exc) from exc
                if isinstance(exc, InputUnavailable):
                    status = self.controller.status()
                    status["executed_steps"] = get_step_count()
                    code = (
                        "CONTROL_UNAVAILABLE"
                        if status["state"] == "unavailable"
                        else "CONTROL_PREEMPTED"
                    )
                    raise self._blocked(ControlBlocked(code, status)) from exc
                raise
            finally:
                current_steps.reset(steps_token)
                current_token.reset(context_token)
                self.controller.end_call(token)
        finally:
            self._call_lock.release()
