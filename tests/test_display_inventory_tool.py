import asyncio
import json
from collections.abc import Callable

from windows_mcp.tools import display as display_tool_module
from windows_mcp.uia import DisplayInfo, Rect


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
            )
        ]


def test_display_inventory_returns_display_dpi_metadata() -> None:
    mcp = FakeMCP()
    display_tool_module.register(mcp, get_desktop=FakeDesktop, get_analytics=lambda: None)

    result = json.loads(asyncio.run(mcp.tools["DisplayInventory"]()))

    assert result == {
        "displays": [
            {
                "index": 0,
                "device_name": "\\\\.\\DISPLAY1",
                "primary": True,
                "rect": {
                    "left": 0,
                    "top": 0,
                    "right": 1920,
                    "bottom": 1080,
                    "width": 1920,
                    "height": 1080,
                },
                "work_rect": {
                    "left": 0,
                    "top": 0,
                    "right": 1920,
                    "bottom": 1040,
                    "width": 1920,
                    "height": 1040,
                },
                "effective_dpi": 144,
                "scale": 1.5,
            }
        ],
        "count": 1,
    }
