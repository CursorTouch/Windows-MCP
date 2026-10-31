"""App/process allowlist matching and target extraction for tool calls."""

from __future__ import annotations

import json
from pathlib import PureWindowsPath


def normalize_app(name: str) -> str:
    """Normalize a process image, path or Start Menu name: basename, lowercase, no .exe."""
    base = PureWindowsPath(name.strip().strip('"')).name.lower()
    return base[:-4] if base.endswith(".exe") else base


def is_allowed(name: str | None, allowed: list[str]) -> bool:
    """Whether *name* matches an allowlist entry after normalization."""
    if not name:
        return False
    return normalize_app(name) in {normalize_app(a) for a in allowed}


def parse_point(raw: object) -> tuple[int, int] | None:
    """Parse a ``loc`` value (list or JSON/"x,y" string) into a point, or None."""
    if raw is None:
        return None
    value = raw
    if isinstance(raw, str):
        text = raw.strip()
        try:
            value = json.loads(text)
        except ValueError:
            value = [p for p in text.strip("[]()").split(",")]
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(float(value[0])), int(float(value[1]))
        except TypeError, ValueError:
            return None
    return None


def parse_points(raw: object) -> list[tuple[int, int]] | None:
    """Parse ``locs`` (list of points, possibly as a JSON string). None if unparsable."""
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(value, (list, tuple)):
        return None
    points = []
    for item in value:
        # MultiEdit uses [x, y, text]; keep the coordinates.
        p = parse_point(list(item[:2])) if isinstance(item, (list, tuple)) else None
        if p is None:
            return None
        points.append(p)
    return points
