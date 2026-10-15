"""Black-box opt-out test: real MCP subprocess and harmless PowerShell output.

No UI tools, physical input injection, hooks or control windows are used.
"""

import asyncio
import os
from pathlib import Path
import sys

import psutil
import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["relaxed", "balanced", "strict"])
async def test_disabled_stdio_background_command_and_status(tmp_path, policy):
    root = Path(__file__).resolve().parents[1]
    config = tmp_path / "config.toml"
    config.write_text("[control]\nenabled = true\nidle_timeout_seconds = 25\n", encoding="utf-8")
    before = {p.pid for p in psutil.Process().children(recursive=True)}
    transport = StdioTransport(
        command=sys.executable,
        args=[
            "-m",
            "windows_mcp",
            "serve",
            "--config",
            str(config),
            "--no-control",
            "--control-policy",
            policy,
            "--control-idle-timeout",
            "2.5",
            "--tools",
            "ControlStatus,PowerShell",
        ],
        cwd=str(root),
        keep_alive=False,
        env={
            **os.environ,
            "ANONYMIZED_TELEMETRY": "false",
            "WINDOWS_MCP_WATCHDOG": "false",
            "PYTHONPATH": str(root / "src"),
        },
    )
    children = set()
    async with Client(transport, timeout=20) as client:
        children = {p.pid for p in psutil.Process().children(recursive=True)} - before
        assert children, "test must exercise an external server process"
        names = {tool.name for tool in await client.list_tools()}
        assert names == {"ControlStatus", "PowerShell"}
        status = await client.call_tool("ControlStatus")
        assert status.data["state"] == "disabled"
        assert status.data["enabled"] is False
        assert status.data["policy"] == policy
        assert status.data["idle_timeout_seconds"] == 2.5
        for _ in range(2):
            result = await client.call_tool(
                "PowerShell", {"command": "Write-Output 'control-opt-out-ok'"}
            )
            assert not result.is_error
            text = "\n".join(part.text for part in result.content if hasattr(part, "text"))
            assert "control-opt-out-ok" in text
            assert "Status Code: 0" in text
        assert (await client.call_tool("ControlStatus")).data["state"] == "disabled"
    for _ in range(20):
        if not any(psutil.pid_exists(pid) for pid in children):
            break
        await asyncio.sleep(0.05)
    assert not any(psutil.pid_exists(pid) for pid in children), (
        "MCP child must exit after client close"
    )
