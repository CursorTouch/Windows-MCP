"""The server must release stateful pointer input without skipping other cleanup."""

import asyncio

import pytest

from windows_mcp import __main__ as server
from windows_mcp import infrastructure, tools
from windows_mcp.desktop import control, control_overlay, service
from windows_mcp.tools import control_notifications


@pytest.mark.parametrize("close_fails", [False, True])
@pytest.mark.parametrize("notifier_close_fails", [False, True])
def test_lifespan_closes_pointer_before_watchdog_and_analytics(
    close_fails: bool,
    notifier_close_fails: bool,
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

        def add_middleware(self, middleware: object) -> None:
            pass

    class FakeControl:
        mouse_takeover_units = 120
        mouse_takeover_pixels = 120
        _thread = None

        def subscribe(self, callback: object) -> None:
            pass

        def set_health_probe(self, callback: object) -> None:
            pass

        def start(self) -> None:
            events.append("controller.start")

        def stop(self) -> None:
            events.append("controller.stop")

    class FakeNotifier:
        def __init__(self, controller: object) -> None:
            pass

        def start(self) -> None:
            events.append("notifier.start")

        async def close(self) -> None:
            events.append("notifier.close")
            if notifier_close_fails:
                raise OSError("notifier close failed")

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
    monkeypatch.setattr(control, "get_controller", lambda: FakeControl())
    monkeypatch.setattr(control_notifications, "ControlNotifier", FakeNotifier)
    monkeypatch.setattr(control_overlay, "start", lambda: events.append("overlay.start"))
    monkeypatch.setattr(control_overlay, "stop", lambda: events.append("overlay.stop"))
    monkeypatch.setattr(control_overlay, "is_healthy", lambda: True)

    mcp = server._build_mcp()

    async def run_lifespan() -> None:
        async with mcp.lifespan(mcp):
            pass

    if notifier_close_fails:
        with pytest.raises(OSError, match="notifier close failed"):
            asyncio.run(run_lifespan())
    else:
        asyncio.run(run_lifespan())

    assert events == [
        "overlay.start",
        "controller.start",
        "notifier.start",
        "notifier.close",
        "desktop.close",
        "controller.stop",
        "overlay.stop",
        "watchdog.stop",
        "analytics.close",
    ]
    if close_fails:
        assert "Failed to release desktop input during shutdown" in caplog.text
