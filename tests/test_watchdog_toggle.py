"""Tests for the WINDOWS_MCP_WATCHDOG opt-in switch (issue #332).

The watchdog used to default on. It now has to be asked for: its only
surviving consumer is `Tree.on_focus_change`, which debounces and writes a
debug log line, while running it costs an STA thread and a UIA event
subscription that can take the whole server down via a native access
violation in the pump.
"""

import pytest

from windows_mcp import __main__ as wm


class TestOffByDefault:
    def test_unset_leaves_it_off(self, monkeypatch):
        monkeypatch.delenv("WINDOWS_MCP_WATCHDOG", raising=False)
        assert wm._watchdog_enabled() is False

    @pytest.mark.parametrize(
        "value", ["off", "0", "false", "no", "disabled", "OFF", "  False  ", "No", ""]
    )
    def test_disabling_and_unrecognised_values_stay_off(self, monkeypatch, value):
        monkeypatch.setenv("WINDOWS_MCP_WATCHDOG", value)
        assert wm._watchdog_enabled() is False, value


class TestExplicitOptIn:
    @pytest.mark.parametrize(
        "value", ["on", "1", "true", "yes", "enabled", "ON", "  True  ", "Yes"]
    )
    def test_enabling_values_turn_it_on(self, monkeypatch, value):
        monkeypatch.setenv("WINDOWS_MCP_WATCHDOG", value)
        assert wm._watchdog_enabled() is True, value

    def test_manifest_boolean_maps_through(self, monkeypatch):
        """manifest.json passes the user_config boolean through as a string."""
        monkeypatch.setenv("WINDOWS_MCP_WATCHDOG", "true")
        assert wm._watchdog_enabled() is True

        monkeypatch.setenv("WINDOWS_MCP_WATCHDOG", "false")
        assert wm._watchdog_enabled() is False
