"""Keep both output streams and report command failures to MCP clients."""

from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.powershell import service
from windows_mcp.powershell.service import PowerShellExecutor
from windows_mcp.tools.shell import register


@pytest.mark.parametrize("as_bytes", [False, True])
@pytest.mark.parametrize(
    ("stdout", "stderr", "code"),
    [
        ("output", "diagnostic", 0),
        ("output", "diagnostic", 1),
        ("", "diagnostic", 1),
        ("output", "", 0),
        ("", "", 0),
    ],
)
def test_executor_retains_both_streams(monkeypatch, stdout, stderr, code, as_bytes):
    monkeypatch.setattr(service, "_prepare_env", lambda: {})
    monkeypatch.setattr(service, "_resolve_shell", lambda shell, env: "powershell")
    monkeypatch.setattr(service, "is_elevated", lambda: False)
    monkeypatch.setattr(
        service,
        "run_with_graceful_timeout",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=stdout.encode() if as_bytes else stdout,
            stderr=stderr.encode() if as_bytes else stderr,
            returncode=code,
        ),
    )
    output, status = PowerShellExecutor.execute_command("unused")
    assert output == stdout + (("\n[stderr]\n" if stdout else "") + stderr if stderr else "")
    assert status == code


def test_stderr_access_denied_hint_survives_stdout(monkeypatch):
    monkeypatch.setattr(service, "_prepare_env", lambda: {})
    monkeypatch.setattr(service, "_resolve_shell", lambda shell, env: "powershell")
    monkeypatch.setattr(service, "is_elevated", lambda: False)
    monkeypatch.setattr(
        service,
        "run_with_graceful_timeout",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=b"partial output", stderr=b"Access is denied", returncode=1
        ),
    )
    output, status = PowerShellExecutor.execute_command("unused")
    assert status == 1
    assert "partial output" in output and "Access is denied" in output
    assert "Administrator" in output


@pytest.mark.parametrize("status", [0, 1, 23])
async def test_exit_status_reaches_mcp_client(monkeypatch, status):
    monkeypatch.setattr(
        PowerShellExecutor,
        "execute_command",
        lambda *args, **kwargs: ("output\n[stderr]\ndiagnostic", status),
    )
    mcp = FastMCP("shell-output-test")
    register(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    async with Client(mcp) as client:
        result = await client.call_tool("PowerShell", {"command": "unused"}, raise_on_error=False)
    assert result.is_error is (status != 0)
    text = "\n".join(block.text for block in result.content if block.type == "text")
    assert "output" in text and "diagnostic" in text
    assert str(status) in text
