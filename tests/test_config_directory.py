from windows_mcp.infrastructure import config as config_module


def _clear_config_env(monkeypatch):
    for name in [
        "WINDOWS_MCP_CONFIG_DIR",
        "USERPROFILE",
        "HOME",
        "LOCALAPPDATA",
        "APPDATA",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_config_dir_uses_userprofile(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert config_module._resolve_config_dir() == tmp_path / ".windows-mcp"


def test_config_dir_falls_back_without_home(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert config_module._resolve_config_dir() == tmp_path / "windows-mcp"


def test_explicit_config_dir_has_priority(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    configured = tmp_path / "custom-config"
    monkeypatch.setenv("WINDOWS_MCP_CONFIG_DIR", str(configured))

    assert config_module._resolve_config_dir() == configured
