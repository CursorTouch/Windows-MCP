"""Black-box MCP recovery with a real subprocess and simulated missed release.

The child uses the production recovery sampler, coordinator and MCP gate, with
an asyncio monitor and simulated physical states; no hooks or input injection.
The production Win32 pump is covered separately by the monitor integration test.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import time

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport

from windows_mcp.desktop.control import ControlCoordinator
from windows_mcp.desktop.control_recovery import StaleInputRecovery
from windows_mcp.tools.control_notifications import ControlNotifier, ControlToolGate


def run_server(marker: Path):
    owner = ControlCoordinator()
    owner._state = "user"
    owner._last_user = owner._last_physical_event = time.monotonic() - 20
    owner._pressed.add(0xA4)  # Down was seen; its matching up never arrived.

    @asynccontextmanager
    async def lifespan(app):
        async def monitor():
            recovery = StaleInputRecovery()
            while True:
                recovery.sample(
                    owner, time.monotonic(), lambda vk: 0 if marker.exists() else 0x8000
                )
                owner._handle(("tick",))
                await asyncio.sleep(0.02)

        worker = asyncio.create_task(monitor())
        try:
            yield
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    app = FastMCP("recovery-black-box", lifespan=lifespan)
    app.add_middleware(ControlToolGate(owner, ControlNotifier(owner)))

    @app.tool(name="ControlStatus")
    def status():
        return owner.status()

    @app.tool(name="Probe")
    def probe():
        return "recovered"

    app.run(transport="stdio")


@pytest.mark.asyncio
async def test_stdio_call_recovers_without_server_restart(tmp_path):
    marker = tmp_path / "released"
    transport = StdioTransport(
        command=sys.executable,
        args=[str(Path(__file__).resolve()), str(marker)],
        keep_alive=False,
    )
    async with Client(transport, timeout=15) as client:
        status = await client.call_tool("ControlStatus")
        assert status.data["state"] == "user"
        assert status.data["wait_seconds"] == 0
        result = await client.call_tool("Probe", raise_on_error=False)
        assert result.is_error
        marker.write_text("released", encoding="utf-8")
        for _ in range(50):
            status = await client.call_tool("ControlStatus")
            if status.data["state"] == "ready":
                break
            await asyncio.sleep(0.1)
        assert status.data["state"] == "ready"
        result = await client.call_tool("Probe")
        assert not result.is_error
        assert "recovered" in str(result.content)


if __name__ == "__main__":
    run_server(Path(sys.argv[1]))
