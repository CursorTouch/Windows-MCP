"""Tool names given to --tools, --exclude-tools, their env vars and [tools] exclude."""

import click
import pytest
from click.testing import CliRunner
from fastmcp import FastMCP

from windows_mcp import __main__ as cli

ALL_TOOLS = {"Click", "ControlStatus", "PowerShell", "Screenshot"}


def _server() -> FastMCP:
    mcp = FastMCP("test")

    @mcp.tool(name="ControlStatus")
    def control_status() -> str:
        return "status"

    @mcp.tool(name="Click")
    def click_tool() -> str:
        return "click"

    @mcp.tool(name="PowerShell")
    def powershell() -> str:
        return "powershell"

    @mcp.tool(name="Screenshot")
    def screenshot() -> str:
        return "screenshot"

    return mcp


async def _names(mcp: FastMCP) -> set[str]:
    return {tool.name for tool in await mcp.list_tools()}


@pytest.mark.parametrize("name", ["PowerShell", "powershell", "POWERSHELL"])
async def test_exclude_matches_tool_names_ignoring_case(name):
    mcp = _server()
    cli._apply_tool_filter(mcp, explicit_tools=None, exclude_tools=[name])
    assert await _names(mcp) == ALL_TOOLS - {"PowerShell"}


@pytest.mark.parametrize("names", [["Screenshot", "Click"], ["screenshot", "CLICK"]])
async def test_tools_match_tool_names_ignoring_case(names):
    mcp = _server()
    cli._apply_tool_filter(mcp, explicit_tools=names, exclude_tools=None)
    assert await _names(mcp) == {"ControlStatus", "Screenshot", "Click"}


@pytest.mark.parametrize(
    "explicit_tools,exclude_tools,unknown",
    [
        (None, ["PowerShel"], "PowerShel"),
        (["Screenshot", "Clik"], None, "Clik"),
        (["Screenshot", "Clik", "Snapshot"], None, "Clik, Snapshot"),
    ],
)
async def test_unknown_tool_name_is_an_error(explicit_tools, exclude_tools, unknown):
    mcp = _server()
    with pytest.raises(click.ClickException) as excinfo:
        cli._apply_tool_filter(mcp, explicit_tools=explicit_tools, exclude_tools=exclude_tools)
    assert excinfo.value.message == (
        f"Unknown tools: {unknown}. Allowed: Click, ControlStatus, PowerShell, Screenshot"
    )
    assert await _names(mcp) == ALL_TOOLS


@pytest.mark.parametrize("source", ["flag", "env", "config"])
def test_unknown_tool_name_never_starts_server(monkeypatch, tmp_path, caplog, source):
    mcp = _server()
    started = []
    monkeypatch.setattr(mcp, "run", lambda **kwargs: started.append(kwargs))
    monkeypatch.setattr(cli, "_build_mcp", lambda: mcp)
    monkeypatch.setattr(cli, "_control_notifier", None)
    monkeypatch.setattr(cli, "install_selfpipe_guard", lambda: None)
    monkeypatch.setattr(cli.asyncio, "set_event_loop_policy", lambda _policy: None)
    config = tmp_path / "config.toml"
    config.write_text(
        '[tools]\nexclude = ["PowerShel"]\n' if source == "config" else "", encoding="utf-8"
    )
    args = ["serve", "--config", str(config)]
    if source == "flag":
        args += ["--exclude-tools", "PowerShel"]
    env = {"WINDOWS_MCP_TOOLS": None, "WINDOWS_MCP_EXCLUDE_TOOLS": None}
    if source == "env":
        env["WINDOWS_MCP_EXCLUDE_TOOLS"] = "PowerShel"

    result = CliRunner().invoke(cli.main, args, env=env)

    assert result.exit_code == 1
    assert result.output == (
        "Error: Unknown tools: PowerShel. Allowed: Click, ControlStatus, PowerShell, Screenshot\n"
    )
    assert started == []
    # A usage error, not a crash: no "Server exiting due to unhandled exception" traceback.
    assert "Server exiting" not in caplog.text
