from pathlib import Path

import pytest

from windows_mcp.infrastructure.config import (
    ControlConfig,
    WindowsMCPConfig,
    load_config,
    write_config,
)


def test_default_control_thresholds():
    assert load_config(None).control == ControlConfig(120, 120)


def test_control_thresholds_round_trip(tmp_path: Path):
    config = WindowsMCPConfig()
    config.control.mouse_takeover_units = 240
    config.control.mouse_takeover_pixels = 180
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
