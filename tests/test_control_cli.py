"""Startup control options use the existing CLI > env > TOML merge contract."""

import pytest
from click.testing import CliRunner

from windows_mcp import __main__ as cli
from windows_mcp.infrastructure.config import ControlConfig


@pytest.fixture
def invoke(monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    original = '[control]\nenabled = false\nidle_timeout_seconds = 4.5\npolicy = "balanced"\n'
    path.write_text(original, encoding="utf-8")
    captured = []
    monkeypatch.setattr(cli, "_control_config", ControlConfig())
    monkeypatch.setattr(cli, "install_selfpipe_guard", lambda: None)
    monkeypatch.setattr(cli.asyncio, "set_event_loop_policy", lambda _: None)
    monkeypatch.setattr(cli, "_run_server", lambda **kw: captured.append(cli._control_config))

    def run(args=(), env=None):
        result = CliRunner().invoke(
            cli.main,
            ["serve", "--config", str(path), *args],
            env={
                "WINDOWS_MCP_CONTROL": None,
                "WINDOWS_MCP_CONTROL_POLICY": None,
                "WINDOWS_MCP_CONTROL_IDLE_TIMEOUT": None,
                **(env or {}),
            },
        )
        assert path.read_text(encoding="utf-8") == original
        return result, captured

    return run


@pytest.mark.parametrize(
    "args,env,enabled,timeout",
    [
        ([], {}, False, 4.5),
        ([], {"WINDOWS_MCP_CONTROL": "true", "WINDOWS_MCP_CONTROL_IDLE_TIMEOUT": "2"}, True, 2),
        (
            ["--control", "--control-idle-timeout", "1.5"],
            {"WINDOWS_MCP_CONTROL": "false", "WINDOWS_MCP_CONTROL_IDLE_TIMEOUT": "2"},
            True,
            1.5,
        ),
        (["--no-control"], {"WINDOWS_MCP_CONTROL": "true"}, False, 4.5),
    ],
)
def test_startup_precedence(invoke, args, env, enabled, timeout):
    result, captured = invoke(args, env)
    assert result.exit_code == 0, result.output
    assert captured[0].enabled is enabled
    assert captured[0].idle_timeout_seconds == timeout


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "true", "garbage"])
@pytest.mark.parametrize("source", ["cli", "env"])
def test_invalid_timeout_never_starts_server(invoke, value, source):
    result, captured = invoke(
        ["--control-idle-timeout", value] if source == "cli" else [],
        {"WINDOWS_MCP_CONTROL_IDLE_TIMEOUT": value} if source == "env" else {},
    )
    assert result.exit_code != 0
    assert not captured


def test_invalid_enabled_environment_never_starts_server(invoke):
    result, captured = invoke(env={"WINDOWS_MCP_CONTROL": "perhaps"})
    assert result.exit_code != 0
    assert not captured


def test_help_explains_restart_and_defaults():
    result = CliRunner().invoke(cli.main, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--control / --no-control" in result.output
    assert "--control-idle-timeout" in result.output
    assert "Restart required" in result.output


@pytest.mark.parametrize(
    "args,env,expected",
    [
        ([], {}, "balanced"),
        ([], {"WINDOWS_MCP_CONTROL_POLICY": "relaxed"}, "relaxed"),
        (["--control-policy", "strict"], {"WINDOWS_MCP_CONTROL_POLICY": "balanced"}, "strict"),
    ],
)
def test_policy_startup_precedence(invoke, args, env, expected):
    result, captured = invoke(args, env)
    assert result.exit_code == 0, result.output
    assert captured[0].policy == expected


@pytest.mark.parametrize("source", ["cli", "env"])
def test_invalid_policy_never_starts(invoke, source):
    result, captured = invoke(
        ["--control-policy", "unknown"] if source == "cli" else [],
        {"WINDOWS_MCP_CONTROL_POLICY": "unknown"} if source == "env" else {},
    )
    assert result.exit_code != 0
    assert not captured
