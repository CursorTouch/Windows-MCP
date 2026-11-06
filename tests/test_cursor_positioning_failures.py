"""Do not report successful input after Windows rejects cursor positioning."""

import ctypes
from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.desktop import service
from windows_mcp.desktop.control_ledger import InputLedger
from windows_mcp.desktop.service import Desktop
from windows_mcp.tools.input import register
from windows_mcp.uia import core


@pytest.fixture
def rejected_position(monkeypatch: pytest.MonkeyPatch) -> Mock:
    def reject(x: int, y: int) -> int:
        ctypes.windll.kernel32.SetLastError(5)
        return 0

    position = Mock(side_effect=reject)
    monkeypatch.setattr(core.ctypes.windll.user32, "SetCursorPos", position)
    return position


def test_set_cursor_pos_preserves_success(monkeypatch: pytest.MonkeyPatch) -> None:
    position = Mock(return_value=1)
    monkeypatch.setattr(core.ctypes.windll.user32, "SetCursorPos", position)

    assert core.SetCursorPos(-20, 40) is True
    position.assert_called_once_with(-20, 40)


def test_set_cursor_pos_reports_windows_error(rejected_position: Mock) -> None:
    with pytest.raises(OSError, match=r"SetCursorPos\(100, 200\)") as exc:
        core.SetCursorPos(100, 200)

    assert exc.value.winerror == 5
    assert "input desktop" in str(exc.value)
    assert "elevated" in str(exc.value)


def test_failure_without_extended_error_does_not_report_stale_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core.ctypes.windll.user32, "SetCursorPos", lambda x, y: 0)
    ctypes.windll.kernel32.SetLastError(123)

    with pytest.raises(OSError, match="did not provide an error code") as exc:
        core.SetCursorPos(100, 200)

    assert getattr(exc.value, "winerror", None) != 123


@pytest.mark.parametrize(
    "helper",
    ["Click", "RightClick", "MiddleClick", "PressMouse", "RightPressMouse", "MiddlePressMouse"],
)
def test_failed_positioning_does_not_inject_button_events(
    monkeypatch: pytest.MonkeyPatch,
    rejected_position: Mock,
    helper: str,
) -> None:
    event = Mock()
    monkeypatch.setattr(core, "mouse_event", event)
    monkeypatch.setattr(core, "GetVirtualScreenRect", lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)

    with pytest.raises(OSError):
        getattr(core, helper)(100, 200, waitTime=0)

    event.assert_not_called()


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("Move", {"loc": [100, 200]}),
        ("Click", {"loc": [100, 200]}),
        ("Click", {"loc": [100, 200], "clicks": 0}),
        ("Type", {"loc": [100, 200], "text": "must not be typed"}),
    ],
)
@pytest.mark.asyncio
async def test_positioning_failure_is_mcp_error(
    monkeypatch: pytest.MonkeyPatch,
    rejected_position: Mock,
    tool: str,
    arguments: dict,
) -> None:
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = None
    monkeypatch.setattr(service, "get_controller", lambda: Mock())
    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    monkeypatch.setattr(core, "GetCursorPos", lambda: (0, 0))
    monkeypatch.setattr(core, "GetVirtualScreenRect", lambda: (0, 0, 1920, 1080))
    event = Mock()
    keys = Mock()
    monkeypatch.setattr(core, "mouse_event", event)
    monkeypatch.setattr(core, "SendKeys", keys)
    server = FastMCP("cursor-failure-test")
    register(server, get_desktop=lambda: desktop, get_analytics=lambda: None)

    async with Client(server) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)

    assert result.is_error
    assert "SetCursorPos" in result.content[0].text
    event.assert_not_called()
    keys.assert_not_called()


def test_drag_releases_button_after_native_positioning_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = None
    owner = Mock()
    owner.input_ledger = InputLedger()
    owner.physical_mouse_down.return_value = False
    monkeypatch.setattr(service, "get_controller", lambda: owner)
    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(core, "GetVirtualScreenRect", lambda: (0, 0, 1920, 1080))
    position = Mock(side_effect=[1, 0])
    monkeypatch.setattr(core.ctypes.windll.user32, "SetCursorPos", position)
    event = Mock()
    monkeypatch.setattr(core, "mouse_event", event)

    with pytest.raises(OSError, match="SetCursorPos"):
        desktop.drag([100, 200], from_loc=[10, 20], duration=0.1)

    assert position.call_count == 2
    assert event.call_count == 2
    assert event.call_args_list[0].args[0] & core.MouseEventFlag.LeftDown
    assert event.call_args_list[1].args[0] & core.MouseEventFlag.LeftUp
    assert owner.input_ledger.pending() == ()
