"""Tests for the server.stateless_http config knob added alongside the
new --stateless-http CLI flag.

The flag exists so a streamable-http MCP server can be restarted (e.g.
the host machine reboots, or a horizontal-scaler replaces the pod)
without rejecting reconnecting clients with "Session not found". When
the knob is on, FastMCP is run with stateless_http=True so no
Mcp-Session-Id header is issued and the server keeps no per-connection
state.
"""

from pathlib import Path

import pytest

from windows_mcp.infrastructure.config import (
    ServerConfig,
    WindowsMCPConfig,
    load_config,
    write_config,
)


def test_default_is_false():
    cfg = ServerConfig()
    assert cfg.stateless_http is False


def test_load_stateless_http_true(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[server]\n'
        'transport = "streamable-http"\n'
        'stateless_http = true\n',
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.server.stateless_http is True
    assert cfg.server.transport == "streamable-http"


def test_load_stateless_http_false_explicit(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[server]\n'
        'stateless_http = false\n',
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.server.stateless_http is False


def test_load_stateless_http_rejects_non_bool(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[server]\n'
        'stateless_http = "yes"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="server.stateless_http"):
        load_config(p)


def test_write_config_emits_stateless_http_when_set(tmp_path: Path):
    cfg = WindowsMCPConfig()
    cfg.server.stateless_http = True
    out = tmp_path / "out.toml"
    write_config(cfg, out)
    text = out.read_text(encoding="utf-8")
    assert "stateless_http = true" in text


def test_write_config_omits_stateless_http_when_default(tmp_path: Path):
    cfg = WindowsMCPConfig()
    # Tweak a non-default value so [server] section is emitted at all,
    # then verify stateless_http stays absent.
    cfg.server.transport = "streamable-http"
    out = tmp_path / "out.toml"
    write_config(cfg, out)
    text = out.read_text(encoding="utf-8")
    assert "stateless_http" not in text


def test_load_stateless_http_default_when_absent(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text('[server]\nhost = "localhost"\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.server.stateless_http is False


def test_run_server_passes_stateless_http_kwarg(monkeypatch):
    """When _run_server is called for streamable-http it must forward
    stateless_http through to FastMCP's mcp.run()."""
    from windows_mcp import __main__ as wm

    captured: dict = {}

    class _FakeMCP:
        def run(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(wm, "_build_mcp", lambda: _FakeMCP())
    monkeypatch.setattr(wm, "_apply_tool_filter", lambda *a, **k: None)

    wm._run_server(
        transport="streamable-http",
        host="127.0.0.1",
        port=8765,
        stateless_http=True,
    )
    assert captured.get("stateless_http") is True
    assert captured.get("transport") == "streamable-http"


def test_run_server_stateless_default_false(monkeypatch):
    from windows_mcp import __main__ as wm

    captured: dict = {}

    class _FakeMCP:
        def run(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(wm, "_build_mcp", lambda: _FakeMCP())
    monkeypatch.setattr(wm, "_apply_tool_filter", lambda *a, **k: None)

    wm._run_server(
        transport="streamable-http",
        host="127.0.0.1",
        port=8765,
    )
    assert captured.get("stateless_http") is False
