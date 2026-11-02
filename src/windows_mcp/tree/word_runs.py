"""Optional merging of per-word text leaves into one leaf per line run.

Electron/Chromium apps often report one UI Automation word range per glyph
(every CJK character becomes its own ``Word`` node), which can make up most of
a Snapshot. With ``WINDOWS_MCP_MERGE_WORD_NODES`` enabled, adjacent words on the
same text line are merged into a single node whose box covers the whole run.
"""

from __future__ import annotations

import os
import unicodedata
from typing import Protocol, TypeVar


class _RectLike(Protocol):
    left: int
    top: int
    right: int
    bottom: int


R = TypeVar("R", bound=_RectLike)

_TRUE_VALUES = {"1", "true", "yes", "on"}


def merge_word_nodes_enabled(env: "os._Environ[str] | dict[str, str] | None" = None) -> bool:
    """Return True when ``WINDOWS_MCP_MERGE_WORD_NODES`` is set to a true value."""
    source = os.environ if env is None else env
    return source.get("WINDOWS_MCP_MERGE_WORD_NODES", "").strip().lower() in _TRUE_VALUES


def _is_wide(char: str) -> bool:
    return unicodedata.east_asian_width(char) in ("W", "F")


def _joiner(left: str, right: str) -> str:
    # CJK text has no spaces between glyphs; Latin words need one.
    if not left or not right or _is_wide(left[-1]) or _is_wide(right[0]):
        return ""
    return " "


def _same_line(a: _RectLike, b: _RectLike) -> bool:
    overlap = min(a.bottom, b.bottom) - max(a.top, b.top)
    height = min(a.bottom - a.top, b.bottom - b.top)
    return height > 0 and overlap * 2 >= height


def _adjacent(a: _RectLike, b: _RectLike) -> bool:
    height = max(a.bottom - a.top, b.bottom - b.top)
    gap = b.left - a.right
    # Next word starts after the previous one and no further than ~one glyph away.
    return b.left >= a.left and gap <= max(2, height)


def merge_word_runs(words: list[tuple[str, R]]) -> list[tuple[str, R]]:
    """Merge consecutive ``(text, rect)`` words that sit next to each other on one line.

    The input order (reading order from UI Automation) is kept. A new rect of the
    same type as the inputs, built as ``type(rect)(left, top, right, bottom)``,
    covers each merged run.
    """
    merged: list[tuple[str, R]] = []
    for text, rect in words:
        if merged:
            prev_text, prev_rect = merged[-1]
            if _same_line(prev_rect, rect) and _adjacent(prev_rect, rect):
                box = type(prev_rect)(
                    min(prev_rect.left, rect.left),
                    min(prev_rect.top, rect.top),
                    max(prev_rect.right, rect.right),
                    max(prev_rect.bottom, rect.bottom),
                )
                merged[-1] = (prev_text + _joiner(prev_text, text) + text, box)
                continue
        merged.append((text, rect))
    return merged
