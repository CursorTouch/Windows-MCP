"""Kill switch: a flag file and/or a global hotkey that block every tool call."""

from __future__ import annotations

import logging
import os
import threading

from windows_mcp.safety.config import KillSwitchSafetyConfig

logger = logging.getLogger(__name__)


class KillSwitch:
    """Engaged when the flag file exists or after the hotkey was pressed.

    The hotkey latches: once pressed, calls stay blocked until the server is
    restarted. The flag file is checked on every call, so creating it blocks the
    next action and deleting it releases the block (unless the hotkey latched).
    """

    def __init__(self, cfg: KillSwitchSafetyConfig, hotkey_factory=None) -> None:
        self.cfg = cfg
        self._latched = threading.Event()
        self._listener = None
        self._hotkey_factory = hotkey_factory

    def engage(self) -> None:
        if not self._latched.is_set():
            logger.warning("Kill switch engaged by hotkey: all tool calls are now refused")
        self._latched.set()

    def start(self) -> None:
        if not self.cfg.enabled or not self.cfg.hotkey or self._hotkey_factory is None:
            return
        try:
            self._listener = self._hotkey_factory(self.cfg.hotkey, self.engage)
            self._listener.start()
            logger.info("Kill-switch hotkey: %s", self.cfg.hotkey)
        except Exception:
            logger.exception("Kill-switch hotkey unavailable; the flag file still works")

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass

    def engaged(self) -> str | None:
        """Return why the switch is engaged, or None."""
        if not self.cfg.enabled:
            return None
        if self._latched.is_set():
            return f"Kill switch engaged by hotkey {self.cfg.hotkey}; restart the server to resume."
        if self.cfg.flag_file and os.path.exists(self.cfg.flag_file):
            return (
                f"Kill switch engaged: flag file {self.cfg.flag_file} exists; delete it to resume."
            )
        return None
