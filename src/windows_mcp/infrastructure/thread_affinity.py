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
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import threading
from typing import Callable, TypeVar


T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="windows-mcp-uia")


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


async def run_uia_affine(func: Callable[..., T], *args, **kwargs) -> T:
    """Run ``func`` on the dedicated UIA worker without blocking the event loop."""
    if is_uia_thread():
        return func(*args, **kwargs)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, partial(func, *args, **kwargs))
