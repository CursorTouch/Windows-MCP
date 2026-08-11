"""Tests for the WINDOWS_MCP_WATCHDOG opt-out switch (issue #332)."""

from windows_mcp import __main__ as wm


class TestWatchdogEnabled:
    def test_enabled_by_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("WINDOWS_MCP_WATCHDOG", raising=False)
        assert wm._watchdog_enabled() is True

    def test_disabling_values_turn_it_off(self, monkeypatch):
        for value in ["off", "0", "false", "no", "disabled", "OFF", "  False  ", "No"]:
            monkeypatch.setenv("WINDOWS_MCP_WATCHDOG", value)
            assert wm._watchdog_enabled() is False, value

    def test_other_values_keep_it_on(self, monkeypatch):
        # Anything not in the disabling set (including empty string) keeps the
        # default-on behavior unchanged.
        for value in ["on", "1", "true", "yes", "enabled", ""]:
            monkeypatch.setenv("WINDOWS_MCP_WATCHDOG", value)
            assert wm._watchdog_enabled() is True, value
