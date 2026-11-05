import sys
import types
from pathlib import Path

import pytest

if sys.platform != "win32" and "windows_mcp.infrastructure" not in sys.modules:
    # The package __init__ starts the UIA worker; load only the pure submodules off-Windows.
    _pkg = types.ModuleType("windows_mcp.infrastructure")
    _pkg.__path__ = [str(Path(__file__).parents[1] / "src" / "windows_mcp" / "infrastructure")]
    sys.modules["windows_mcp.infrastructure"] = _pkg

from windows_mcp.infrastructure.config import WindowsMCPConfig, load_config, write_config
from windows_mcp.safety.config import parse_safety_config
from windows_mcp.safety.transport import check_http_exposure
from windows_mcp.infrastructure.auth import is_loopback_host


def test_defaults_are_all_on():
    cfg = parse_safety_config(None)
    assert cfg.enabled and cfg.apps.enforce and cfg.confirm.enabled
    assert cfg.audit.enabled and not cfg.audit.screenshot and cfg.kill_switch.enabled
    assert cfg.powershell.mode == "disabled"
    assert cfg.confirm.timeout_seconds == 30.0
    assert cfg.kill_switch.hotkey == "ctrl+alt+shift+k"


@pytest.mark.parametrize(
    "data",
    [
        {"apps": {"enforce": "yes"}},
        {"apps": {"allowed": "notepad"}},
        {"apps": {"alowed": ["x"]}},
        {"powershell": {"mode": "open"}},
        {"confirm": {"timeout_seconds": 0}},
        {"confirm": {"timeout_seconds": True}},
        {"unknown": {}},
        {"audit": []},
    ],
)
def test_invalid_rejected(data):
    with pytest.raises(ValueError):
        parse_safety_config(data)


def test_load_and_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[server]\nrequire_auth = false\n\n[safety.apps]\nallowed = ["code.exe"]\n\n'
        '[safety.powershell]\nmode = "allowlist"\nallowed_commands = ["Get-Date"]\n\n'
        '[safety.audit]\nscreenshot = true\npath = "~/a.jsonl"\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.server.require_auth is False and cfg.server.allow_remote_bind is False
    assert cfg.safety.apps.allowed == ["code.exe"]
    assert cfg.safety.powershell.mode == "allowlist"
    assert cfg.safety.audit.screenshot and not cfg.safety.audit.path.startswith("~")
    cfg.server.auth_key = "k"
    out = tmp_path / "out.toml"
    write_config(cfg, out)
    again = load_config(out)
    assert again.safety.apps.allowed == ["code.exe"] and again.server.auth_key == "k"
    assert again.server.require_auth is False


def test_defaults_without_file():
    assert WindowsMCPConfig().server.require_auth is True


def _check(**kw):
    base = dict(
        transport="streamable-http",
        host="localhost",
        auth_key=None,
        oauth_configured=False,
        require_auth=True,
        allow_remote_bind=False,
        is_loopback=is_loopback_host,
    )
    base.update(kw)
    return check_http_exposure(**base)


def test_transport_policy():
    assert _check(transport="stdio", host="0.0.0.0") is None
    assert "bearer" in _check()
    assert _check(auth_key="t") is None
    assert _check(oauth_configured=True, transport="sse") is None
    assert _check(require_auth=False) is None
    assert "localhost" in _check(host="0.0.0.0", auth_key="t")
    assert "localhost" in _check(host="192.168.1.5", auth_key="t")
    assert _check(host="192.168.1.5", auth_key="t", allow_remote_bind=True) is None
    assert _check(host="::1", auth_key="t") is None
