import asyncio
import json
from collections.abc import Callable

import pytest

from windows_mcp.desktop import service
from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import Status, Window
from windows_mcp.tools import window as window_tool_module
from windows_mcp.tree.views import BoundingBox


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


def _desktop() -> Desktop:
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = None
    return desktop


def _window(title: str, handle: int = 100, process_id: int = 200) -> Window:
    return Window(
        name=title,
        is_browser=False,
        depth=0,
        status=Status.NORMAL,
        bounding_box=BoundingBox(left=0, top=0, right=100, bottom=100, width=100, height=100),
        handle=handle,
        process_id=process_id,
    )


def _patch_window_api(monkeypatch: pytest.MonkeyPatch, *, foreground: int = 100) -> list[tuple]:
    moves: list[tuple] = []
    monkeypatch.setattr(service.win32gui, "IsWindow", lambda handle: handle == 100)
    monkeypatch.setattr(service.win32gui, "GetWindowRect", lambda handle: (10, 20, 210, 170))
    monkeypatch.setattr(service.win32gui, "GetClientRect", lambda handle: (0, 0, 180, 120))
    monkeypatch.setattr(service.win32gui, "ClientToScreen", lambda handle, point: (20, 50))
    monkeypatch.setattr(service.win32gui, "GetForegroundWindow", lambda: foreground)
    monkeypatch.setattr(
        service.win32gui,
        "MoveWindow",
        lambda *args: moves.append(args),
    )
    monkeypatch.setattr(
        service, "Process", lambda pid: type("P", (), {"name": lambda self: "app.exe"})()
    )
    return moves


def test_find_exact_windows_filters_by_process_and_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _desktop()
    _patch_window_api(monkeypatch)
    monkeypatch.setattr(desktop, "get_windows", lambda: ([_window("Target App")], {100}))

    result = desktop.find_exact_windows(title="target", process="APP.EXE")

    assert result == [
        {
            "handle": 100,
            "process_id": 200,
            "process": "app.exe",
            "title": "Target App",
            "status": "Normal",
            "outer": {
                "left": 10,
                "top": 20,
                "width": 200,
                "height": 150,
                "right": 210,
                "bottom": 170,
            },
            "client": {
                "left": 20,
                "top": 50,
                "width": 180,
                "height": 120,
                "right": 200,
                "bottom": 170,
            },
        }
    ]


def test_activate_exact_window_verifies_foreground(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = _desktop()
    _patch_window_api(monkeypatch, foreground=100)
    monkeypatch.setattr(desktop, "get_windows", lambda: ([_window("Target App")], {100}))
    called: list[int] = []
    monkeypatch.setattr(desktop, "bring_window_to_top", lambda handle: called.append(handle))

    result = desktop.activate_exact_window(handle=100, process_id=200)

    assert result["handle"] == 100
    assert called == [100]


def test_activate_exact_window_fails_when_readback_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _desktop()
    _patch_window_api(monkeypatch, foreground=999)
    monkeypatch.setattr(desktop, "get_windows", lambda: ([_window("Target App")], {100}))
    monkeypatch.setattr(desktop, "bring_window_to_top", lambda handle: None)

    with pytest.raises(ValueError, match="Failed to activate"):
        desktop.activate_exact_window(handle=100, process_id=200)


def test_set_exact_client_bounds_converts_to_outer_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = _desktop()
    moves = _patch_window_api(monkeypatch)
    monkeypatch.setattr(desktop, "get_windows", lambda: ([_window("Target App")], {100}))

    desktop.set_exact_window_bounds(handle=100, process_id=200, client=[30, 70, 300, 200])

    assert moves == [(100, 20, 40, 320, 230, True)]


def test_window_tool_find_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    desktop = _desktop()
    monkeypatch.setattr(
        desktop,
        "find_exact_windows",
        lambda **kwargs: [{"handle": 100, "title": "Target App"}],
    )
    mcp = FakeMCP()
    window_tool_module.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)

    result = json.loads(asyncio.run(mcp.tools["Window"](mode="find", title="Target")))

    assert result == {"windows": [{"handle": 100, "title": "Target App"}], "count": 1}


def test_window_tool_bounds_requires_handle() -> None:
    mcp = FakeMCP()
    window_tool_module.register(mcp, get_desktop=_desktop, get_analytics=lambda: None)

    with pytest.raises(ValueError, match="handle is required"):
        asyncio.run(mcp.tools["Window"](mode="bounds", outer=[0, 0, 100, 100]))
