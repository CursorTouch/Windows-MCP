from windows_mcp.powershell.service import _prepare_env


def test_prepare_env_does_not_leak_pythonhome(monkeypatch):
    monkeypatch.setenv("PYTHONHOME", r"C:\uv\python")

    env = _prepare_env()

    assert "PYTHONHOME" not in env
