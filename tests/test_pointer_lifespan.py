"""The server must release stateful pointer input without skipping other cleanup."""

import asyncio

import pytest

from windows_mcp import __main__ as server
from windows_mcp import infrastructure, tools
from windows_mcp.desktop import service


@pytest.mark.parametrize("close_fails", [False, True])
def test_lifespan_closes_pointer_before_watchdog_and_analytics(
    close_fails: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []

    class FakeDesktop:
        def get_screen_size(self) -> tuple[int, int]:
            return (100, 100)

        def close(self) -> None:
            events.append("desktop.close")
            if close_fails:
                raise OSError("release failed")

    class FakeWatchdog:
        def stop(self) -> None:
            events.append("watchdog.stop")

    class FakeAnalytics:
        async def close(self) -> None:
            events.append("analytics.close")

    class FakeMCP:
        def __init__(self, **kwargs: object) -> None:
            self.lifespan = kwargs["lifespan"]

    desktop = FakeDesktop()
    watchdog = FakeWatchdog()
    analytics = FakeAnalytics()
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "true")
    monkeypatch.setattr(server, "_mcp", None)
    monkeypatch.setattr(server, "desktop", None)
    monkeypatch.setattr(server, "watchdog", None)
    monkeypatch.setattr(server, "analytics", None)
    monkeypatch.setattr(server, "FastMCP", FakeMCP)
    monkeypatch.setattr(server, "_start_watchdog", lambda _: watchdog)
    monkeypatch.setattr(infrastructure, "PostHogAnalytics", lambda: analytics)
    monkeypatch.setattr(service, "Desktop", lambda: desktop)
    monkeypatch.setattr(tools, "register_all", lambda *args, **kwargs: None)

    mcp = server._build_mcp()

    async def run_lifespan() -> None:
        async with mcp.lifespan(mcp):
            pass

    asyncio.run(run_lifespan())

    assert events == ["desktop.close", "watchdog.stop", "analytics.close"]
    if close_fails:
        assert "Failed to release desktop input during shutdown" in caplog.text
