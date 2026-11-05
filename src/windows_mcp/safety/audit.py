"""Append-only JSONL audit log of every tool call and safety decision."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime

from windows_mcp.safety.config import AuditSafetyConfig

logger = logging.getLogger(__name__)


def redact(arguments: dict | None, keys: list[str], max_len: int) -> dict:
    """Copy *arguments* with sensitive keys masked and long strings truncated."""
    lowered = {k.lower() for k in keys}
    out: dict = {}
    for key, value in (arguments or {}).items():
        if key.lower() in lowered and value is not None:
            out[key] = f"<redacted {len(str(value))} chars>"
        elif isinstance(value, str) and len(value) > max_len:
            out[key] = value[:max_len] + f"...<{len(value) - max_len} more>"
        else:
            try:
                json.dumps(value)
                out[key] = value
            except TypeError:
                out[key] = repr(value)[:max_len]
    return out


class AuditLog:
    """Thread-safe JSONL writer. A write failure is logged, never raised."""

    def __init__(self, cfg: AuditSafetyConfig, screenshot_fn=None) -> None:
        self.cfg = cfg
        self.screenshot_fn = screenshot_fn
        self._lock = threading.Lock()

    def screenshot(self, tool: str) -> str | None:
        if not (self.cfg.enabled and self.cfg.screenshot and self.screenshot_fn):
            return None
        stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f")
        return self.screenshot_fn(os.path.join(self.cfg.screenshot_dir, f"{stamp}-{tool}.png"))

    def write(self, record: dict) -> None:
        if not self.cfg.enabled:
            return
        entry = {"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"), **record}
        line = json.dumps(entry, ensure_ascii=False, default=str)
        try:
            with self._lock:
                os.makedirs(os.path.dirname(self.cfg.path) or ".", exist_ok=True)
                with open(self.cfg.path, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
        except OSError:
            logger.exception("Could not write audit log %s", self.cfg.path)
