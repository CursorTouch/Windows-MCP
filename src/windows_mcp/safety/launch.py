"""Allowlist checks on the executable that actually gets launched.

The App tool's ``launch`` mode picks a Start Menu entry by fuzzy name match, so the
requested name ("notepad") can end up starting something else ("notepad++.exe").
``LaunchGuard`` checks the resolved target instead of the requested name, and checks
the started process afterwards. ``resolve_command`` resolves bare executable names
through PATH only (never the current directory).
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from windows_mcp.safety.apps import is_allowed
from windows_mcp.safety.config import SafetyConfig

logger = logging.getLogger(__name__)


def _has_dir_part(text: str) -> bool:
    return any(sep in text for sep in ("/", "\\")) or (len(text) > 1 and text[1] == ":")


def resolve_command(
    executable: str,
    *,
    path_env: str | None = None,
    which: Callable[..., str | None] = shutil.which,
) -> str | None:
    """Return the absolute path of *executable*, or None if it cannot be found.

    A value with a directory part is resolved as a path. A bare name ("notepad.exe",
    "notepad") is searched only in the absolute PATH directories, with PATHEXT applied by
    ``shutil.which``. Each directory is passed to ``which`` as part of the name, because
    ``which`` on Windows otherwise also searches the current directory first.
    """
    text = executable.strip().strip('"')
    if not text:
        return None
    expanded = os.path.expanduser(text)
    if _has_dir_part(expanded):
        path = Path(expanded).resolve()
        return str(path) if path.is_file() else None
    search = os.environ.get("PATH", "") if path_env is None else path_env
    for directory in search.split(os.pathsep):
        directory = directory.strip().strip('"')
        if not directory or not os.path.isabs(directory):
            continue  # Relative PATH entries would depend on the current directory.
        hit = which(os.path.join(directory, text))
        if hit:
            return os.path.abspath(hit)
    return None


class LaunchGuard:
    """Checks a Start Menu launch on what it resolves to and on what actually started.

    *platform* supplies ``resolve_app_target(appid) -> str | None``,
    ``process_exe(pid) -> str | None`` and ``terminate_process(pid) -> bool``.
    """

    def __init__(self, cfg: SafetyConfig, platform: Any) -> None:
        self.cfg = cfg
        self.platform = platform

    def check_target(self, requested: str, matched: str, appid: str) -> str | None:
        """Return an error message if the matched Start Menu entry must not start."""
        try:
            exe = self.platform.resolve_app_target(appid)
        except Exception:  # Resolution is best effort; any failure means deny.
            logger.exception("Could not resolve Start Menu entry %r", appid)
            exe = None
        if not exe:
            return (
                f"Safety: {requested!r} matched Start Menu entry {matched!r} ({appid}), "
                "but its executable could not be determined, so it was not launched."
            )
        if not is_allowed(exe, self.cfg.apps.allowed):
            return (
                f"Safety: {requested!r} matched Start Menu entry {matched!r}, which runs "
                f"{exe}; that executable is not in safety.apps.allowed. Not launched."
            )
        return None

    def verify_launched(self, requested: str, pid: int) -> str | None:
        """Check the process we started. Return an error message if it is not allowed.

        Only the exact pid returned by the launch is ever terminated. Without a pid
        (packaged apps started through shell:AppsFolder) nothing is checked here,
        because no process can be attributed to this launch with certainty.
        """
        if pid <= 0:
            return None
        exe = self.platform.process_exe(pid)
        if exe is None:
            return None  # Already exited (for example a launcher that handed off).
        if is_allowed(exe, self.cfg.apps.allowed):
            return None
        killed = self.platform.terminate_process(pid)
        state = "terminated" if killed else "could NOT be terminated"
        return (
            f"Safety: launching {requested!r} started {exe} (pid {pid}), which is not in "
            f"safety.apps.allowed; that process {state}."
        )


_guard: LaunchGuard | None = None


def set_launch_guard(guard: LaunchGuard | None) -> None:
    global _guard
    _guard = guard


def get_launch_guard() -> LaunchGuard | None:
    return _guard
