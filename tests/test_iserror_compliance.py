"""Regression tests for isError-compliance in Windows-MCP tool handlers.

6 of 19 @mcp.tool() handlers caught `except Exception` and returned a
formatted `'Error: ...'` string, which FastMCP wraps as success content
with `isError=false`. The fix replaces each such return with a bare
`raise` so the original exception propagates and FastMCP sets
`isError=true` on the wire.

Reference: https://composio.dev/blog/mcp-security-vulnerabilities (Dayna
Blackwell MCP security audit, June 2026).
"""

import asyncio
import pytest

try:
    from fastmcp import FastMCP
except ImportError:
    FastMCP = None

try:
    from fastmcp.exceptions import ToolError
except ImportError:  # fastmcp not on the test platform
    ToolError = None  # type: ignore[misc,assignment]

pytestmark = pytest.mark.skipif(
    FastMCP is None or ToolError is None,
    reason="fastmcp not installed; test is non-Windows or fastmcp missing",
)


@pytest.fixture(scope="module")
def mcp():
    _mcp = FastMCP(name="windows-mcp")
    return _mcp


def test_shell_tool_error_is_error_true(monkeypatch, mcp):
    """Shell tool failure must surface as ToolError → isError=true on wire."""
    from windows_mcp.tools.shell import register as shell_tool_reg
    from windows_mcp.powershell import PowerShellExecutor

    shell_tool_reg(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    error_msg = "command rejected"

    def _raise(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError(error_msg)

    monkeypatch.setattr(PowerShellExecutor, "execute_command", _raise)
    with pytest.raises(ToolError) as exc_info:
        asyncio.run(mcp.call_tool("PowerShell", {"command": "bad-cmd"}))
    assert error_msg in str(exc_info.value)


def test_clipboard_tool_error_is_error_true(monkeypatch, mcp):
    """Clipboard tool failure must surface as ToolError."""
    from windows_mcp.tools.clipboard import register as clipboard_tool_reg
    import win32clipboard

    clipboard_tool_reg(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    error_msg = "clipboard locked"

    def _raise(*args, **kwargs):
        raise RuntimeError(error_msg)

    monkeypatch.setattr(win32clipboard, "SetClipboardText", _raise)
    with pytest.raises(ToolError) as exc_info:
        asyncio.run(mcp.call_tool("Clipboard", {"mode": "set", "text": "x"}))
    assert error_msg in str(exc_info.value)


def test_registry_tool_error_is_error_true(monkeypatch, mcp):
    """Registry tool failure must surface as ToolError."""
    from windows_mcp.tools.registry import register as registry_tool_reg
    from windows_mcp import registry

    registry_tool_reg(mcp, get_desktop=lambda: None, get_analytics=lambda: None)

    error_msg = "access denied"

    def _raise(*args, **kwargs):  # noqa: ARG001
        raise PermissionError(error_msg)

    monkeypatch.setattr(registry, "get_value", _raise)
    with pytest.raises(ToolError) as exc_info:
        asyncio.run(mcp.call_tool("Registry", {"mode": "get", "path": "HKLM\\X", "name": "Nope"}))
    assert error_msg in str(exc_info.value)
