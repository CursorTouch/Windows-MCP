"""Verify startup wiring without installing hooks or showing any windows."""

from unittest.mock import Mock

import pytest

from windows_mcp import __main__ as cli
from windows_mcp.desktop import control, control_overlay, service
from windows_mcp.infrastructure.config import ControlConfig
from windows_mcp.tools import control_status
from windows_mcp.tools.control_notifications import ControlNotifier


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_lifespan_wires_effective_settings(monkeypatch, enabled):
    owner = control.ControlCoordinator()
    cfg = ControlConfig(enabled=enabled, idle_timeout_seconds=2.5, policy="balanced")
    calls = []
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "false")
    monkeypatch.setattr(cli, "_mcp", None)
    monkeypatch.setattr(cli, "_control_notifier", None)
    monkeypatch.setattr(cli, "desktop", None)
    monkeypatch.setattr(cli, "_control_config", cfg)
    monkeypatch.setattr(cli, "_start_watchdog", lambda desktop: None)
    monkeypatch.setattr(service, "Desktop", lambda: Mock(get_screen_size=lambda: (100, 100)))
    monkeypatch.setattr(control, "get_controller", lambda: owner)
    monkeypatch.setattr(control_status, "get_controller", lambda: owner)
    monkeypatch.setattr(control_overlay, "start", lambda: calls.append("overlay-start"))
    monkeypatch.setattr(control_overlay, "stop", lambda: calls.append("overlay-stop"))
    monkeypatch.setattr(ControlNotifier, "start", lambda self: calls.append("notifier-start"))
    if enabled:
        monkeypatch.setattr(owner, "start", lambda: calls.append("controller-start"))
        monkeypatch.setattr(owner, "stop", lambda: calls.append("controller-stop"))
    else:
        monkeypatch.setattr(control, "_interactive_desktop", Mock(side_effect=AssertionError))

    mcp = cli._build_mcp()
    async with mcp._lifespan(mcp):
        assert owner.enabled is enabled
        assert owner.policy == "balanced"
        assert owner.idle_timeout_seconds == 2.5
        if enabled:
            assert owner._health_probe is control_overlay.is_healthy
        else:
            assert owner.status()["state"] == "disabled"
            assert owner._thread is owner._watchdog is None
            assert owner._health_probe is None
            # Retained across calls and status queries, released at server exit.
            owner.input_ledger.press(
                "key", lambda: None, lambda: calls.append("release"), lambda: False
            )
            owner.status()
            assert owner.input_ledger.pending() == ("key",)
    assert calls == (
        ["overlay-start", "controller-start", "notifier-start", "controller-stop", "overlay-stop"]
        if enabled
        else ["release"]
    )


@pytest.mark.asyncio
async def test_failed_enabled_start_cleans_up_without_disabling(monkeypatch):
    owner = control.ControlCoordinator()
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "false")
    monkeypatch.setattr(cli, "_mcp", None)
    monkeypatch.setattr(cli, "_control_notifier", None)
    monkeypatch.setattr(cli, "desktop", None)
    monkeypatch.setattr(cli, "_control_config", ControlConfig())
    monkeypatch.setattr(cli, "_start_watchdog", lambda desktop: None)
    monkeypatch.setattr(service, "Desktop", lambda: Mock(get_screen_size=lambda: (100, 100)))
    monkeypatch.setattr(control, "get_controller", lambda: owner)
    monkeypatch.setattr(control_overlay, "start", Mock(side_effect=RuntimeError("no indicator")))
    stopped = Mock()
    monkeypatch.setattr(control_overlay, "stop", stopped)
    mcp = cli._build_mcp()
    with pytest.raises(RuntimeError, match="no indicator"):
        async with mcp._lifespan(mcp):
            pytest.fail("startup must not proceed")
    assert owner.status()["enabled"] is True
    assert owner.status()["state"] == "unavailable"
    stopped.assert_called_once()
