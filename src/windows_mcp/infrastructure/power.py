"""Windows power-state helpers for long-running MCP server sessions."""

from __future__ import annotations

import atexit
import ctypes
import logging
import os

logger = logging.getLogger(__name__)

ES_SYSTEM_REQUIRED = 0x00000001
ES_CONTINUOUS = 0x80000000

_KEEP_AWAKE_VALUES = {"1", "true", "yes", "on", "enabled"}
_keep_awake_active = False
_cleanup_registered = False


def _keep_awake_enabled() -> bool:
    value = os.getenv("WINDOWS_MCP_KEEP_AWAKE")
    return value is not None and value.strip().lower() in _KEEP_AWAKE_VALUES


def _set_thread_execution_state(flags: int) -> None:
    """Apply a Windows execution-state request to the current thread."""
    result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
    if not result:
        raise OSError("SetThreadExecutionState failed")


def restore_system_sleep() -> None:
    """Clear the continuous system-required request if this process set one."""
    global _keep_awake_active

    if not _keep_awake_active:
        return
    try:
        _set_thread_execution_state(ES_CONTINUOUS)
    except OSError:
        # Process/thread exit clears the request anyway. Do not turn a clean
        # server shutdown into a failure solely because explicit cleanup failed.
        logger.warning(
            "Could not explicitly restore the normal Windows sleep policy",
            exc_info=True,
        )
    finally:
        _keep_awake_active = False


def install_keep_awake_from_env() -> None:
    """Prevent system sleep for this server process when explicitly requested.

    ``WINDOWS_MCP_KEEP_AWAKE`` is opt-in. Enabling it requests
    ``ES_CONTINUOUS | ES_SYSTEM_REQUIRED`` for the server's main thread, which
    keeps Windows from entering idle system sleep while the MCP server is
    running. It intentionally does not set ``ES_DISPLAY_REQUIRED``, so the
    monitor may still power down normally.

    Windows automatically clears a thread's execution-state request when that
    thread exits. We also register an explicit cleanup callback so normal
    shutdown restores the default policy immediately.
    """
    global _cleanup_registered, _keep_awake_active

    if not _keep_awake_enabled() or _keep_awake_active:
        return

    _set_thread_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    _keep_awake_active = True
    if not _cleanup_registered:
        atexit.register(restore_system_sleep)
        _cleanup_registered = True
    logger.info("Windows system sleep prevention enabled for the MCP server lifetime")
