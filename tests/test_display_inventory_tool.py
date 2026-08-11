import asyncio
from types import SimpleNamespace
from collections.abc import Callable

import pytest

from windows_mcp.tools import display as display_tool_module
from windows_mcp.uia import DisplayInfo, Rect, core


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


class FakeDesktop:
    def get_displays(self) -> list[DisplayInfo]:
        return [
            DisplayInfo(
                index=0,
                device_name="\\\\.\\DISPLAY1",
                rect=Rect(0, 0, 1920, 1080),
                primary=True,
                work_rect=Rect(0, 0, 1920, 1040),
                effective_dpi=144,
                scale=1.5,
                orientation="landscape",
            )
        ]


def test_display_inventory_returns_display_dpi_metadata() -> None:
    mcp = FakeMCP()
    display_tool_module.register(mcp, get_desktop=FakeDesktop, get_analytics=lambda: None)

    result = asyncio.run(mcp.tools["DisplayInventory"]())

    assert result == [
        {
            "index": 0,
            "device": "\\\\.\\DISPLAY1",
            "primary": True,
            "bounds": {
                "left": 0,
                "top": 0,
                "right": 1920,
                "bottom": 1080,
                "width": 1920,
                "height": 1080,
            },
            "work_area": {
                "left": 0,
                "top": 0,
                "right": 1920,
                "bottom": 1040,
                "width": 1920,
                "height": 1040,
            },
            "resolution": "1920x1080",
            "orientation": "landscape",
            "effective_dpi": 144,
            "scale": 1.5,
        }
    ]


def test_monitor_dpi_falls_back_to_system_dpi(monkeypatch: pytest.MonkeyPatch) -> None:
    shcore = SimpleNamespace(GetDpiForMonitor=lambda *args: 1)
    user32 = SimpleNamespace(GetDpiForSystem=lambda: 120)
    monkeypatch.setattr(core.ctypes, "windll", SimpleNamespace(shcore=shcore, user32=user32))

    assert core._get_monitor_effective_dpi(123) == (120, 1.25)


def test_display_orientation_uses_current_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    def enum_display_settings(device_name: str, mode: object, dev_mode: object) -> int:
        core.ctypes.memmove(
            core.ctypes.addressof(dev_mode) + 172,
            core.struct.pack("<II", 1080, 1920),
            8,
        )
        return 1

    user32 = SimpleNamespace(EnumDisplaySettingsW=enum_display_settings)
    monkeypatch.setattr(core.ctypes, "windll", SimpleNamespace(user32=user32))

    assert core._get_display_orientation("\\\\.\\DISPLAY1", Rect(0, 0, 1080, 1920)) == "portrait"


def test_display_orientation_falls_back_to_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    user32 = SimpleNamespace(EnumDisplaySettingsW=lambda *args: 0)
    monkeypatch.setattr(core.ctypes, "windll", SimpleNamespace(user32=user32))

    assert core._get_display_orientation("DISPLAY0", Rect(0, 0, 1920, 1080)) == "landscape"
