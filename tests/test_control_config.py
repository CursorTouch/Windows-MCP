from pathlib import Path

import pytest

from windows_mcp.infrastructure.config import (
    ControlConfig,
    WindowsMCPConfig,
    load_config,
    write_config,
)


def test_default_control_thresholds():
    assert load_config(None).control == ControlConfig(500, 500)
    assert load_config(None).control.policy == "relaxed"


def test_control_thresholds_round_trip(tmp_path: Path):
    config = WindowsMCPConfig()
    config.control.mouse_takeover_units = 240
    config.control.mouse_takeover_pixels = 180
    config.control.enabled = False
    config.control.idle_timeout_seconds = 2.5
    path = tmp_path / "config.toml"
    write_config(config, path)
    assert load_config(path).control == config.control


@pytest.mark.parametrize("key", ["mouse_takeover_units", "mouse_takeover_pixels"])
@pytest.mark.parametrize("value", ["true", '"120"', "39", "2001"])
def test_control_thresholds_reject_invalid(tmp_path: Path, key: str, value: str):
    path = tmp_path / "config.toml"
    path.write_text(f"[control]\n{key} = {value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=f"control.{key}"):
        load_config(path)


@pytest.mark.parametrize("value", ["true", '"3"', "0", "-1", "nan", "inf", "-inf"])
def test_idle_timeout_rejects_invalid(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(f"[control]\nidle_timeout_seconds = {value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="control.idle_timeout_seconds"):
        load_config(path)


@pytest.mark.parametrize("value", ["0", "1", '"false"'])
def test_control_enabled_requires_boolean(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(f"[control]\nenabled = {value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="control.enabled"):
        load_config(path)


def test_control_defaults_omitted_on_write(tmp_path):
    path = tmp_path / "config.toml"
    write_config(WindowsMCPConfig(), path)
    assert "[control]" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("policy", ["relaxed", "balanced", "strict"])
def test_policy_round_trip(tmp_path, policy):
    cfg = WindowsMCPConfig()
    cfg.control.policy = policy
    path = tmp_path / "policy.toml"
    write_config(cfg, path)
    assert load_config(path).control.policy == policy


@pytest.mark.parametrize("value", ['"unknown"', '"A"', "true", "3", "[]", "{}"])
def test_invalid_policy(tmp_path, value):
    path = tmp_path / "policy.toml"
    path.write_text("[control]\npolicy = " + value, encoding="utf-8")
    with pytest.raises(ValueError, match="control.policy"):
        load_config(path)
