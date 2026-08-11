"""End-to-end stdio transport tests against a real server subprocess.

Every other test in this suite calls tool functions in-process, which skips
the MCP lifecycle entirely. These tests spawn ``windows-mcp serve`` over a
real stdio pipe and drive it the way a client does, so they cover the layer
in between: the initialize handshake, JSON-RPC framing, and the advertised
tool surface.

The handshake is easy to get wrong by hand. A client that sends a request
before completing::

    --> initialize
    <-- (response)
    --> notifications/initialized

gets ``-32602 Invalid request parameters`` on stdout and
``Failed to validate request: Received request before initialization was
complete`` on stderr. ``test_request_before_initialize_is_rejected`` pins
that behaviour so the failure mode stays recognizable instead of being
mistaken for a server-side startup race.

Requires Windows: the server imports comtypes and pywin32 at startup.
"""

import asyncio
import json
import os
import sys

import pytest

try:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
except ImportError:  # fastmcp not on the test platform
    Client = None
    StdioTransport = None

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or Client is None,
    reason="stdio integration test requires Windows and fastmcp",
)

# Every tool the server is expected to advertise. Doubles as a regression
# guard on the tool surface: adding or renaming a tool must update this list.
EXPECTED_TOOLS = {
    "App",
    "Click",
    "Clipboard",
    "DisplayInventory",
    "FileSystem",
    "Move",
    "MultiEdit",
    "MultiSelect",
    "Notification",
    "PowerShell",
    "Process",
    "Registry",
    "Scrape",
    "Screenshot",
    "Scroll",
    "Shortcut",
    "Snapshot",
    "Type",
    "Wait",
    "WaitFor",
}

# Generous enough for a cold interpreter start on CI, short enough to fail
# fast rather than hang the run.
STARTUP_TIMEOUT = 60.0

SERVER_ARGS = ["-m", "windows_mcp", "serve", "--transport", "stdio"]


def _server_env() -> dict[str, str]:
    """Environment for the server subprocess.

    Telemetry and the UIA watchdog are disabled so the tests neither emit
    analytics events nor depend on a live desktop session.
    """
    return os.environ | {
        "ANONYMIZED_TELEMETRY": "false",
        "WINDOWS_MCP_WATCHDOG": "off",
    }


def _transport() -> "StdioTransport":
    """Launch the server with the interpreter currently running the tests."""
    return StdioTransport(command=sys.executable, args=SERVER_ARGS, env=_server_env())


async def test_handshake_completes_and_lists_tools() -> None:
    """A well-formed client completes the handshake and sees every tool."""
    async with Client(_transport()) as client:
        tools = await asyncio.wait_for(client.list_tools(), STARTUP_TIMEOUT)

    assert {tool.name for tool in tools} == EXPECTED_TOOLS


async def test_tool_call_round_trips_over_stdio() -> None:
    """A tool call survives the full client -> pipe -> server -> pipe path.

    Uses Wait, the only state-changing-free tool with a deterministic result.
    """
    async with Client(_transport()) as client:
        result = await asyncio.wait_for(client.call_tool("Wait", {"duration": 1}), STARTUP_TIMEOUT)

    assert result.is_error is False


async def test_request_before_initialize_is_rejected() -> None:
    """Skipping the handshake must fail loudly, not appear to be a startup race.

    Talks raw newline-delimited JSON-RPC rather than using the client, since
    the whole point is to send a request the client would never send.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        *SERVER_ARGS,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_server_env(),
    )

    try:
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        proc.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
        await proc.stdin.drain()

        raw = await asyncio.wait_for(proc.stdout.readline(), STARTUP_TIMEOUT)
        response = json.loads(raw)
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), 10.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    assert response["error"]["code"] == -32602, response
