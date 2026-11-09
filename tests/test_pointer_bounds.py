"""Move, Scroll, drag and MultiSelect must reject a point off the desktop, as Click does.

SetCursorPos clamps an out-of-range point to the nearest reachable pixel, which
is why Click and Type check the point first (#439). Move, Scroll, drag and
MultiSelect did not: they sent their input to the clamped edge pixel and still
answered with the requested point, e.g. "Moved the mouse pointer to (5000,5000)."
Scroll also moved the pointer before it turned down a direction that does not fit
its type, and accepted a wheel count below 1.
"""

import asyncio
from collections.abc import Callable
from unittest.mock import Mock

import pytest

from windows_mcp.desktop import service
from windows_mcp.desktop.control_ledger import InputLedger
from windows_mcp.desktop.service import Desktop
from windows_mcp.tools import input as input_tools
from windows_mcp.tools import multi as multi_tools

# A single 1920x1080 monitor.
SINGLE_MONITOR = (0, 0, 1920, 1080)
# A second monitor left of and above the primary one: points on it are negative
# but perfectly reachable.
SPANNING_MONITORS = (-1920, -200, 3840, 1280)
# Points just past each edge of SINGLE_MONITOR, and two far outside it.
OFF_DESKTOP = [(-1, 500), (500, -1), (1920, 500), (500, 1080), (-10, -10), (5000, 5000)]
INPUT_HELPERS = (
    "SetCursorPos",
    "Click",
    "RightClick",
    "MiddleClick",
    "PressMouse",
    "ReleaseMouse",
    "PressKey",
    "ReleaseKey",
    "WheelUp",
    "WheelDown",
    "SendKeys",
)


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


def _tools(desktop: Desktop) -> dict[str, Callable]:
    mcp = FakeMCP()
    input_tools.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    multi_tools.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> Desktop:
    """A Desktop on SINGLE_MONITOR whose every input call, or wait, fails the test."""
    owner = Mock()
    owner.input_ledger = InputLedger()
    owner.physical_key_down.return_value = False
    owner.physical_mouse_down.return_value = False
    monkeypatch.setattr(service, "get_controller", lambda: owner)
    monkeypatch.setattr(service.uia, "GetVirtualScreenRect", lambda: SINGLE_MONITOR)
    monkeypatch.setattr(service.uia, "GetCursorPos", lambda: (100, 100))
    # Keep Type's focus checks off the real desktop's UIA tree.
    monkeypatch.setattr(service.uia, "GetFocusedControl", lambda: None)
    monkeypatch.setattr(
        service, "sleep", lambda seconds: pytest.fail("a rejected request must not wait")
    )
    for name in INPUT_HELPERS:
        monkeypatch.setattr(
            service.uia,
            name,
            lambda *args, **kwargs: pytest.fail("input must not be sent for a rejected request"),
        )
    instance = Desktop.__new__(Desktop)
    instance.desktop_state = None
    return instance


@pytest.fixture
def recorder(desktop: Desktop, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple]]:
    """Record the input a request on the desktop sends."""
    calls: list[tuple[str, tuple]] = []

    def record(name: str) -> Callable:
        def recorded(*args: object, **kwargs: object) -> None:
            calls.append((name, args))

        return recorded

    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    for name in INPUT_HELPERS:
        monkeypatch.setattr(service.uia, name, record(name))
    return calls


@pytest.fixture
def spanning_desktop(desktop: Desktop, monkeypatch: pytest.MonkeyPatch) -> Desktop:
    """The same Desktop on SPANNING_MONITORS."""
    monkeypatch.setattr(service.uia, "GetVirtualScreenRect", lambda: SPANNING_MONITORS)
    return desktop


def _cursor_positions(calls: list[tuple[str, tuple]]) -> list[tuple]:
    return [args for name, args in calls if name == "SetCursorPos"]


@pytest.mark.parametrize("point", OFF_DESKTOP)
def test_move_rejects_a_point_off_the_desktop(desktop: Desktop, point: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="outside the desktop bounds"):
        desktop.move(point)


@pytest.mark.parametrize("point", OFF_DESKTOP)
def test_scroll_rejects_a_point_off_the_desktop(desktop: Desktop, point: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="outside the desktop bounds"):
        desktop.scroll(loc=point)


@pytest.mark.parametrize("point", OFF_DESKTOP)
def test_drag_rejects_a_drop_point_off_the_desktop(
    desktop: Desktop, point: tuple[int, int]
) -> None:
    with pytest.raises(ValueError, match="outside the desktop bounds"):
        desktop.drag(list(point))


@pytest.mark.parametrize("point", OFF_DESKTOP)
def test_drag_rejects_a_start_point_off_the_desktop(
    desktop: Desktop, point: tuple[int, int]
) -> None:
    with pytest.raises(ValueError, match="outside the desktop bounds"):
        desktop.drag([500, 500], from_loc=list(point))


@pytest.mark.parametrize("press_ctrl", [True, False])
@pytest.mark.parametrize("point", OFF_DESKTOP)
def test_multi_select_rejects_a_point_off_the_desktop_before_any_click(
    desktop: Desktop, point: tuple[int, int], press_ctrl: bool
) -> None:
    with pytest.raises(ValueError, match="outside the desktop bounds"):
        desktop.multi_select(press_ctrl=press_ctrl, locs=[[200, 200], list(point)])


def test_multi_edit_checks_every_point_before_typing(desktop: Desktop) -> None:
    """Type turned the bad point down only after it had cleared and retyped the first field."""
    with pytest.raises(ValueError, match=r"\(5000,5000\) are outside the desktop bounds"):
        desktop.multi_edit([[200, 200, "first"], [5000, 5000, "second"]])


@pytest.mark.parametrize(
    ("tool", "arguments", "point"),
    [
        ("Move", {"loc": [5000, 5000]}, "(5000,5000)"),
        ("Scroll", {"loc": [5000, 5000]}, "(5000,5000)"),
        ("Move", {"loc": [5000, 5000], "drag": True}, "(5000,5000)"),
        ("Move", {"loc": [500, 500], "drag": True, "from_loc": [-50, 300]}, "(-50,300)"),
        ("MultiSelect", {"locs": [[200, 200], [5000, -20]]}, "(5000,-20)"),
        ("MultiSelect", {"locs": [[200, 200], [5000, -20]], "press_ctrl": False}, "(5000,-20)"),
        ("MultiEdit", {"locs": [[200, 200, "first"], [5000, 5000, "second"]]}, "(5000,5000)"),
    ],
)
def test_tools_report_the_point_off_the_desktop_instead_of_success(
    desktop: Desktop, tool: str, arguments: dict[str, object], point: str
) -> None:
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(_tools(desktop)[tool](**arguments))

    assert str(excinfo.value) == (
        f"Coordinates {point} are outside the desktop bounds x=0..1919, y=0..1079"
    )


def test_move_reaches_a_point_on_a_secondary_monitor(
    spanning_desktop: Desktop, recorder: list[tuple[str, tuple]]
) -> None:
    spanning_desktop.move((-1000, -100))

    assert recorder[-1] == ("SetCursorPos", (-1000, -100))


def test_scroll_reaches_a_point_on_a_secondary_monitor(
    spanning_desktop: Desktop, recorder: list[tuple[str, tuple]]
) -> None:
    spanning_desktop.scroll(loc=(-1920, -200), direction="up", wheel_times=2)

    assert _cursor_positions(recorder)[-1] == (-1920, -200)
    assert [call for call in recorder if call[0] != "SetCursorPos"] == [
        ("WheelUp", (1,)),
        ("WheelUp", (1,)),
    ]


def test_drag_from_a_secondary_monitor_to_the_primary_one(
    spanning_desktop: Desktop, recorder: list[tuple[str, tuple]]
) -> None:
    result = spanning_desktop.drag([1919, 1079], from_loc=[-1000, -100])

    assert recorder[0] == ("PressMouse", (-1000, -100))
    assert recorder[-2:] == [("SetCursorPos", (1919, 1079)), ("ReleaseMouse", ())]
    assert result["start"] == [-1000, -100]


def test_multi_select_clicks_points_on_both_monitors(
    spanning_desktop: Desktop, recorder: list[tuple[str, tuple]]
) -> None:
    spanning_desktop.multi_select(press_ctrl=False, locs=[[-1920, -200], [1919, 1079]])

    assert recorder == [("Click", (-1920, -200)), ("Click", (1919, 1079))]


@pytest.mark.parametrize(
    ("scroll_type", "direction", "message"),
    [
        ("vertical", "left", 'Use "up" or "down".'),
        ("vertical", "right", 'Use "up" or "down".'),
        ("horizontal", "up", 'Use "left" or "right".'),
        ("horizontal", "down", 'Use "left" or "right".'),
    ],
)
def test_scroll_rejects_a_direction_of_the_other_type_before_moving(
    desktop: Desktop, scroll_type: str, direction: str, message: str
) -> None:
    with pytest.raises(ValueError) as excinfo:
        desktop.scroll(loc=(300, 300), type=scroll_type, direction=direction)

    assert str(excinfo.value) == f"Invalid direction. {message}"


def test_scroll_rejects_an_unknown_type_before_moving(desktop: Desktop) -> None:
    with pytest.raises(ValueError, match='Invalid type. Use "horizontal" or "vertical".'):
        desktop.scroll(loc=(300, 300), type="diagonal")


@pytest.mark.parametrize("wheel_times", [0, -1, -3])
def test_scroll_rejects_fewer_than_one_wheel_turn_before_moving(
    desktop: Desktop, wheel_times: int
) -> None:
    with pytest.raises(ValueError, match="wheel_times must be 1 or more"):
        desktop.scroll(loc=(300, 300), wheel_times=wheel_times)


@pytest.mark.parametrize(
    "arguments",
    [
        {"loc": [300, 300], "type": "vertical", "direction": "left"},
        {"loc": [300, 300], "wheel_times": 0},
        {"wheel_times": -3},
    ],
)
def test_scroll_tool_reports_a_request_it_cannot_carry_out_as_an_error(
    desktop: Desktop, arguments: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        asyncio.run(_tools(desktop)["Scroll"](**arguments))


def test_scroll_still_moves_and_scrolls_at_a_point_on_the_desktop(
    desktop: Desktop, recorder: list[tuple[str, tuple]]
) -> None:
    result = asyncio.run(
        _tools(desktop)["Scroll"](
            loc=[300, 300], type="horizontal", direction="right", wheel_times=2
        )
    )

    assert result == "Scrolled horizontal right by 2 wheel times at (300,300)."
    assert _cursor_positions(recorder)[-1] == (300, 300)
    assert [name for name, _ in recorder if name != "SetCursorPos"] == [
        "PressKey",
        "WheelDown",
        "WheelDown",
        "ReleaseKey",
    ]
