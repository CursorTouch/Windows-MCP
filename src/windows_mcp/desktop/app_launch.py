"""Start Menu name matching, kept free of Windows imports so it can be unit-tested."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import PureWindowsPath

from fuzzywuzzy import process

logger = logging.getLogger(__name__)


def _stem(value: str) -> str:
    name = PureWindowsPath(value.strip().strip('"')).name.lower()
    for ext in (".exe", ".lnk"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


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
