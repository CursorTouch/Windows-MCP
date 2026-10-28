from __future__ import annotations

import pytest

from windows_mcp.infrastructure import power


@pytest.fixture(autouse=True)
def reset_power_state(monkeypatch):
    monkeypatch.setattr(power, "_keep_awake_active", False)
    monkeypatch.setattr(power, "_cleanup_registered", False)


def test_keep_awake_is_off_by_default(monkeypatch):
    calls: list[int] = []
    monkeypatch.delenv("WINDOWS_MCP_KEEP_AWAKE", raising=False)
    monkeypatch.setattr(power, "_set_thread_execution_state", calls.append)

    power.install_keep_awake_from_env()

    assert calls == []
    assert power._keep_awake_active is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on", "enabled"])
def test_keep_awake_requests_system_required_without_display_required(monkeypatch, value):
    calls: list[int] = []
    registered = []
    monkeypatch.setenv("WINDOWS_MCP_KEEP_AWAKE", value)
    monkeypatch.setattr(power, "_set_thread_execution_state", calls.append)
    monkeypatch.setattr(power.atexit, "register", registered.append)

    power.install_keep_awake_from_env()

    assert calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED]
    assert registered == [power.restore_system_sleep]
    assert power._keep_awake_active is True


def test_keep_awake_install_is_idempotent(monkeypatch):
    calls: list[int] = []
    monkeypatch.setenv("WINDOWS_MCP_KEEP_AWAKE", "true")
    monkeypatch.setattr(power, "_set_thread_execution_state", calls.append)
    monkeypatch.setattr(power.atexit, "register", lambda callback: None)

    power.install_keep_awake_from_env()
    power.install_keep_awake_from_env()

    assert calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED]


def test_restore_clears_continuous_system_required_request(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(power, "_keep_awake_active", True)
    monkeypatch.setattr(power, "_set_thread_execution_state", calls.append)

    power.restore_system_sleep()

    assert calls == [power.ES_CONTINUOUS]
    assert power._keep_awake_active is False


def test_failed_enable_does_not_mark_keep_awake_active(monkeypatch):
    monkeypatch.setenv("WINDOWS_MCP_KEEP_AWAKE", "true")
    monkeypatch.setattr(
        power,
        "_set_thread_execution_state",
        lambda flags: (_ for _ in ()).throw(OSError("boom")),
    )

    with pytest.raises(OSError, match="boom"):
        power.install_keep_awake_from_env()

    assert power._keep_awake_active is False
    assert power._cleanup_registered is False


def test_cleanup_failure_does_not_break_shutdown(monkeypatch, caplog):
    monkeypatch.setattr(power, "_keep_awake_active", True)
    monkeypatch.setattr(
        power,
        "_set_thread_execution_state",
        lambda flags: (_ for _ in ()).throw(OSError("boom")),
    )

    power.restore_system_sleep()

    assert power._keep_awake_active is False
    assert "Could not explicitly restore the normal Windows sleep policy" in caplog.text
