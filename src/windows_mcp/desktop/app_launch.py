"""Start Menu name matching, kept free of Windows imports so it can be unit-tested."""

from __future__ import annotations

from fuzzywuzzy import process


def match_start_menu_app(name: str, apps_map: dict[str, str]) -> tuple[str, str] | None:
    """Return ``(display_name, appid)`` of the best fuzzy match, or None."""
    matched = process.extractOne(name, apps_map.keys(), score_cutoff=70)
    if matched is None:
        return None
    app_name = matched[0]
    appid = apps_map.get(app_name)
    return (app_name, appid) if appid else None
