"""Click/Type must not report success for input that cannot land.

An out-of-range loc used to be accepted silently: SetCursorPos clamps to the
nearest reachable pixel, so the input went somewhere else while the tool still
answered "Typed ... at (5000,5000)" (#439). clear/press_enter honored only the
literal "true", so a plausible "yes" silently meant false (#440).
"""

import asyncio
from collections.abc import Callable

import pytest

from windows_mcp.desktop import service
from windows_mcp.desktop.service import Desktop
from windows_mcp.tools.input import register

# A single 1920x1080 monitor.
SINGLE_MONITOR = (0, 0, 1920, 1080)
# A second monitor left of and above the primary one: points on it are negative
# but perfectly reachable.
SPANNING_MONITORS = (-1920, -200, 3840, 1280)


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


class FakeDesktop:
    def __init__(self) -> None:
        self.desktop_state = object()
        self.type_calls: list[dict[str, object]] = []

    def type(self, **kwargs: object) -> None:
        self.type_calls.append(kwargs)


def _tools(desktop: FakeDesktop) -> dict[str, Callable]:
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> Desktop:
    """A Desktop whose every input call fails the test if it is reached."""
    instance = Desktop.__new__(Desktop)
    instance.desktop_state = None
    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    monkeypatch.setattr(service.uia, "GetVirtualScreenRect", lambda: SINGLE_MONITOR)
    for name in ("Click", "RightClick", "MiddleClick", "SetCursorPos", "SendKeys"):
        monkeypatch.setattr(
            service.uia,
            name,
            lambda *args, **kwargs: pytest.fail("input must not be sent for a rejected request"),
        )
    return instance


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple]]:
    """Record the uia calls a successful request makes."""
    calls: list[tuple[str, tuple]] = []

    def record(name: str) -> Callable:
        def recorded(*args: object, **kwargs: object) -> None:
            calls.append((name, args))

        return recorded

    for name in ("Click", "RightClick", "MiddleClick", "SetCursorPos", "SendKeys"):
        monkeypatch.setattr(service.uia, name, record(name))
    # No focused element: keeps the tests off the real desktop's UIA tree.
    monkeypatch.setattr(service.uia, "GetFocusedControl", lambda: None)
    return calls


@pytest.mark.parametrize(
    "point",
    [
        (-10, -10),
        (5000, 5000),
        (-1, 500),
        (500, -1),
        (1920, 500),
        (500, 1080),
        (1920, 1080),
    ],
)
def test_click_rejects_a_point_off_the_desktop(desktop: Desktop, point: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="outside the desktop bounds"):
        desktop.click(loc=list(point))


@pytest.mark.parametrize("point", [(-10, -10), (5000, 5000)])
def test_type_rejects_a_point_off_the_desktop(desktop: Desktop, point: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="outside the desktop bounds"):
        desktop.type(loc=point, text="NEG")


def test_rejection_names_the_point_and_the_bounds(desktop: Desktop) -> None:
    with pytest.raises(ValueError) as excinfo:
        desktop.click(loc=[5000, 5000])

    message = str(excinfo.value)
    assert "(5000,5000)" in message
    assert "x=0..1919" in message
    assert "y=0..1079" in message


def test_hover_only_click_is_validated_too(desktop: Desktop) -> None:
    """clicks=0 positions the cursor, which clamps just the same."""
    with pytest.raises(ValueError, match="outside the desktop bounds"):
        desktop.click(loc=[5000, 5000], clicks=0)


@pytest.mark.parametrize("point", [(0, 0), (1919, 1079), (960, 540)])
def test_click_accepts_a_point_on_the_desktop(
    desktop: Desktop,
    recorder: list[tuple[str, tuple]],
    point: tuple[int, int],
) -> None:
    desktop.click(loc=list(point))

    assert recorder == [("Click", point)]


@pytest.mark.parametrize("point", [(-1920, -200), (-1000, -100), (1919, 1079)])
def test_negative_coordinates_on_a_secondary_monitor_are_accepted(
    desktop: Desktop,
    recorder: list[tuple[str, tuple]],
    monkeypatch: pytest.MonkeyPatch,
    point: tuple[int, int],
) -> None:
    monkeypatch.setattr(service.uia, "GetVirtualScreenRect", lambda: SPANNING_MONITORS)

    desktop.click(loc=list(point))

    assert recorder == [("Click", point)]


@pytest.mark.parametrize("flag", ["clear", "press_enter"])
@pytest.mark.parametrize("value", ["yes", "no", "1", "0", "y", "tru", "TRUEISH", ""])
def test_type_rejects_a_non_boolean_flag_before_sending_input(
    desktop: Desktop,
    flag: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match=f"{flag} must be true or false"):
        desktop.type(loc=(100, 100), text="EF", **{flag: value})


@pytest.mark.parametrize("value", [True, "true", "True", " TRUE "])
def test_type_honors_every_accepted_true_literal(
    desktop: Desktop,
    recorder: list[tuple[str, tuple]],
    value: bool | str,
) -> None:
    desktop.type(loc=(100, 100), text="EF", press_enter=value)

    assert ("SendKeys", ("{Enter}",)) in recorder


@pytest.mark.parametrize("value", [False, "false", "False", " FALSE "])
def test_type_honors_every_accepted_false_literal(
    desktop: Desktop,
    recorder: list[tuple[str, tuple]],
    value: bool | str,
) -> None:
    desktop.type(loc=(100, 100), text="EF", press_enter=value)

    assert ("SendKeys", ("{Enter}",)) not in recorder


def test_type_clear_selects_the_line_and_deletes_it_without_ctrl_a(
    desktop: Desktop,
    recorder: list[tuple[str, tuple]],
) -> None:
    desktop.type(loc=(100, 100), text="EF", clear="true")

    assert ("SendKeys", ("{Ctrl}a",)) not in recorder
    keys = [args[0] for name, args in recorder if name == "SendKeys"]
    assert keys[:3] == ["{Home}", "{Shift}{End}", "{Back}"]


def test_type_tool_rejects_a_non_boolean_flag_before_calling_the_desktop() -> None:
    fake = FakeDesktop()

    with pytest.raises(ValueError, match="press_enter must be true or false"):
        asyncio.run(_tools(fake)["Type"](text="EF", loc=[100, 100], press_enter="yes"))

    assert fake.type_calls == []


def test_type_tool_passes_parsed_booleans_through() -> None:
    fake = FakeDesktop()

    asyncio.run(_tools(fake)["Type"](text="EF", loc=[100, 100], clear="true", press_enter="false"))

    assert fake.type_calls == [
        {
            "loc": [100, 100],
            "text": "EF",
            "caret_position": "idle",
            "clear": True,
            "press_enter": False,
        }
    ]
