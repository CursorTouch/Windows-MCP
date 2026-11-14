"""Keep the windows of the program that launched this server out of UI Automation.

An MCP client that starts the server over stdio is an ancestor of the server
process. When that client is an Electron app (VS Code, Cursor, Claude Desktop),
UI Automation traffic into its own windows has been seen to leave the app hung,
after which Windows closes it together with the session (#383).

The launching processes are found once, from the server's own parent chain.
Only Win32 and psutil are used: the point is to decide before any UI Automation
call is made against the window.
"""

from __future__ import annotations

import ctypes
import logging
import os
from collections.abc import Mapping
from functools import lru_cache

import psutil
import win32process

logger = logging.getLogger(__name__)

_ENV_VAR = "WINDOWS_MCP_EXCLUDE_HOST_PROCESS"
_OFF_VALUES = {"0", "false", "no", "off"}


def host_exclusion_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether host windows are kept out of UI Automation.

    Args:
        env: Mapping to read from. Defaults to ``os.environ``.

    Returns:
        True unless ``WINDOWS_MCP_EXCLUDE_HOST_PROCESS`` is set to ``0``,
        ``false``, ``no`` or ``off`` (case-insensitive).
    """
    source = os.environ if env is None else env
    return source.get(_ENV_VAR, "").strip().lower() not in _OFF_VALUES


def _shell_process_id() -> int:
    """Return the PID of the desktop shell (normally explorer.exe), or 0 if unknown."""
    try:
        shell_window = ctypes.windll.user32.GetShellWindow()
        if not shell_window:
            return 0
        return win32process.GetWindowThreadProcessId(shell_window)[1]
    except Exception:
        return 0


@lru_cache(maxsize=1)
def host_processes() -> dict[int, float]:
    """Return the processes that launched this server, as ``{pid: creation time}``.

    The chain is followed from the server's parent upwards and stops before the
    desktop shell. A program started from the Start menu or the taskbar is a
    child of the shell, and the shell also owns the taskbar and the desktop,
    which must stay reachable.

    The creation time is kept so that a PID Windows reuses after a launcher has
    exited is not mistaken for the launcher.

    Returns:
        An empty mapping when the parent chain cannot be read.
    """
    shell_pid = _shell_process_id()
    hosts: dict[int, float] = {}
    try:
        for parent in psutil.Process().parents():
            if parent.pid == shell_pid:
                break
            hosts[parent.pid] = parent.create_time()
    except Exception as error:
        logger.debug("[Tree] Could not read the launching process chain: %s", error)
    return hosts


def is_host_window(handle: int) -> bool:
    """Return whether `handle` belongs to a process that launched this server.

    Returns:
        False when the check is switched off, and when the window's owner cannot
        be resolved: such a window is queried and traversed as before.
    """
    if not host_exclusion_enabled():
        return False
    hosts = host_processes()
    if not hosts:
        return False
    try:
        pid = win32process.GetWindowThreadProcessId(handle)[1]
        started = hosts.get(pid)
        if started is None:
            return False
        return psutil.Process(pid).create_time() == started
    except Exception:
        return False
