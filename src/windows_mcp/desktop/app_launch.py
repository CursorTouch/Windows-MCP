"""Start Menu name matching, kept free of Windows imports so it can be unit-tested."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Any

from fuzzywuzzy import process

logger = logging.getLogger(__name__)


def _stem(value: str) -> str:
    name = PureWindowsPath(value.strip().strip('"')).name.lower()
    for ext in (".exe", ".lnk"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


DIRECT_LAUNCH_SUFFIXES = (".exe", ".com", ".lnk")
_WINDOWS_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{2})")


def is_path_like(name: str) -> bool:
    """True if *name* is written as a file path rather than a Start Menu name."""
    text = name.strip().strip('"')
    return "\\" in text or "/" in text or bool(re.match(r"^[A-Za-z]:", text))


def direct_launch_path(name: str) -> str | None:
    """Return the file to start when *name* is a path, None when it is a Start Menu name.

    A path is never fuzzy-matched to a Start Menu entry: it must be an existing absolute
    ``.exe``, ``.com`` or ``.lnk`` file, otherwise ValueError is raised.
    """
    if not is_path_like(name):
        return None
    text = os.path.expandvars(name.strip().strip('"'))
    if not (os.path.isabs(text) or _WINDOWS_ABS.match(text)):
        raise ValueError(f"App path must be absolute: {name}")
    path = Path(text)
    if not path.is_file():
        raise ValueError(f"App path does not exist: {name}")
    if path.suffix.lower() not in DIRECT_LAUNCH_SUFFIXES:
        raise ValueError(f"App path must be an .exe, .com or .lnk file: {name}")
    return str(path.resolve())


def launch_direct_path(
    name: str, path: str, *, guard: Any, launch: Callable[[str], str]
) -> tuple[str, int, int]:
    """Start *path* (a .lnk is started through its target) after the allowlist check.

    *guard* is the LaunchGuard or None; a refusal raises LaunchDenied before anything
    starts. *launch* is ``tools.app._launch_executable`` (re-checks a shortcut's target
    right before start and the started pid afterwards). Returns (message, status, pid).
    """
    if guard is not None:
        from windows_mcp.safety.launch import LaunchDenied

        error = guard.check_path(name, path)
        if error:
            raise LaunchDenied(error)
    result = json.loads(launch(path))
    return f"Started {result['executable']} (pid {result['pid']}).", 0, int(result["pid"])


def match_start_menu_app(
    name: str,
    apps_map: dict[str, str],
    resolve: Callable[[str], str | None] | None = None,
) -> tuple[str, str] | None:
    """Return ``(display_name, appid)`` for *name*, or None.

    Order, so that "notepad" picks the system Notepad (listed as "Blocco note" on Italian
    Windows) and not a fuzzy "notepad++":

    1. exact display name (case-insensitive);
    2. an entry whose executable stem equals *name* ("notepad" = ``...\\notepad.exe``),
       read from the AppID path, or from *resolve* for shortcuts and packaged apps whose
       AppID contains *name* (to keep the number of lookups small);
    3. fuzzy match on display names (score >= 70).
    """
    wanted = name.strip().lower()
    if not wanted:
        return None
    for app_name, appid in apps_map.items():
        if app_name.lower() == wanted and appid:
            return app_name, appid

    stem = _stem(wanted)
    for app_name, appid in apps_map.items():
        if not appid:
            continue
        is_path = "\\" in appid or "/" in appid
        if is_path and not appid.lower().endswith(".lnk"):
            if _stem(appid) == stem:
                return app_name, appid
            continue
        if resolve is None or (stem not in appid.lower() and stem not in app_name.lower()):
            continue
        try:
            target = resolve(appid)
        except Exception:  # A failed lookup just means "not this entry".
            logger.debug("Could not resolve %r while matching %r", appid, name)
            continue
        if target and _stem(target) == stem:
            return app_name, appid

    matched = process.extractOne(name, apps_map.keys(), score_cutoff=70)
    if matched is None:
        return None
    app_name = matched[0]
    appid = apps_map.get(app_name)
    return (app_name, appid) if appid else None
