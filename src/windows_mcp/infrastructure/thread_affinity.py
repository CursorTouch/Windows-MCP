"""Dedicated execution lane for UIAutomation/comtypes thread-affine work.

UI Automation COM objects must be created and used from the same apartment.  The
MCP server itself is async, while most tool bodies are synchronous, so generic
worker-pool dispatch can move consecutive UIA calls across different threads.
Conversely, running those synchronous bodies inline on the event loop preserves
affinity but lets long calls such as WaitFor stall the whole server.

This module owns one long-lived worker thread.  It eagerly imports
``windows_mcp.uia`` on that worker so the process-global automation client is
created in the same apartment that later executes every UIA-affine synchronous
tool body.
"""

from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from typing import Any, Callable, TypeVar


T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="windows-mcp-uia")
_status_lock = threading.Lock()
_busy_since: float | None = None
_abandoned_calls: int = 0
_queued_calls: int = 0
_last_completed_at: float | None = None


class UIAWorkerBusyError(RuntimeError):
    """Raised when the dedicated UIA worker is still occupied by an abandoned call."""


def _initialize_uia_worker() -> int:
    """Initialize the UIAutomation module on the dedicated worker thread."""
    # Importing windows_mcp.uia creates the shared _AutomationClient lazily via
    # the event-handler interface definitions.  Doing that import here makes
    # the client belong to this worker's COM apartment rather than the MCP loop.
    import windows_mcp.uia  # noqa: F401

    return threading.get_ident()


# Start the worker eagerly.  infrastructure is imported before Desktop/service
# during server construction, so this establishes UIA ownership before any
# main-thread import can create the shared automation client.
_UIA_THREAD_ID = _executor.submit(_initialize_uia_worker).result()


def uia_thread_id() -> int:
    """Return the OS thread id that owns UIAutomation work."""
    return _UIA_THREAD_ID


def is_uia_thread() -> bool:
    """Return whether the caller is already on the dedicated UIA worker."""
    return threading.get_ident() == _UIA_THREAD_ID


def uia_worker_status() -> dict[str, Any]:
    """Return out-of-band occupancy metrics for the dedicated UIA worker thread."""
    now = time.monotonic()
    with _status_lock:
        busy = _busy_since is not None
        busy_seconds = round(max(0.0, now - _busy_since), 3) if _busy_since is not None else 0.0
        last_completed_age = (
            round(max(0.0, now - _last_completed_at), 3) if _last_completed_at is not None else None
        )
        return {
            "busy": busy,
            "busy_seconds": busy_seconds,
            "abandoned_calls": _abandoned_calls,
            "queued_calls": _queued_calls,
            "last_completed_age_seconds": last_completed_age,
        }


async def run_uia_affine(func: Callable[..., T], *args, **kwargs) -> T:
    """Run ``func`` on the dedicated UIA worker without blocking the event loop."""
    if is_uia_thread():
        return func(*args, **kwargs)

    global _busy_since, _abandoned_calls, _queued_calls, _last_completed_at
    with _status_lock:
        if _abandoned_calls > 0 and _busy_since is not None:
            busy_for = max(0.0, time.monotonic() - _busy_since)
            raise UIAWorkerBusyError(
                f"Previous UIA call was abandoned and the worker thread is still busy "
                f"(busy for {busy_for:.1f}s; worker state unknown)."
            )
        _queued_calls += 1

    loop = asyncio.get_running_loop()
    # Unlike to_thread, run_in_executor does not propagate request context.
    # Carry control tokens/step counters into this job without leaking bindings
    # into the long-lived worker or a later request.
    context = contextvars.copy_context()
    job_state = {"started": False, "finished": False, "abandoned": False}

    def _run_guarded() -> T:
        global _busy_since, _abandoned_calls, _queued_calls, _last_completed_at
        with _status_lock:
            _queued_calls = max(0, _queued_calls - 1)
            if job_state["abandoned"]:
                job_state["finished"] = True
                raise asyncio.CancelledError()
            job_state["started"] = True
            _busy_since = time.monotonic()
        try:
            return context.run(func, *args, **kwargs)
        finally:
            with _status_lock:
                job_state["finished"] = True
                _busy_since = None
                _last_completed_at = time.monotonic()
                if job_state["abandoned"]:
                    _abandoned_calls = max(0, _abandoned_calls - 1)

    future = loop.run_in_executor(_executor, _run_guarded)
    try:
        return await future
    except asyncio.CancelledError:
        with _status_lock:
            job_state["abandoned"] = True
            if job_state["started"] and not job_state["finished"]:
                _abandoned_calls += 1
        raise
