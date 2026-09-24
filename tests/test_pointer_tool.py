import asyncio
from collections.abc import Callable

import pytest

from windows_mcp.tools.pointer import register


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}
        self.options: dict[str, dict[str, object]] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        self.options[name] = kwargs

        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


class FakeDesktop:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def pointer_down(self, loc, button, timeout) -> dict[str, object]:
        self.calls.append(("down", loc, button, timeout))
        return {"action": "down", "loc": list(loc), "button": button, "timeout": timeout}

    def pointer_move(self, loc, duration) -> dict[str, object]:
        self.calls.append(("move", loc, duration))
        return {"action": "move", "loc": list(loc), "button": "left", "duration": duration}

    def pointer_up(self, button) -> dict[str, object]:
        self.calls.append(("up", button))
        return {"action": "up", "button": button or "left"}

    def pointer_cancel(self) -> dict[str, object]:
        self.calls.append(("cancel",))
        return {"action": "cancel", "button": None}


def _registered(desktop: FakeDesktop) -> tuple[FakeMCP, Callable]:
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp, mcp.tools["Pointer"]


def test_pointer_registers_one_consolidated_tool() -> None:
    mcp, _ = _registered(FakeDesktop())

    assert set(mcp.tools) == {"Pointer"}
    assert mcp.options["Pointer"]["annotations"].destructiveHint is True
    assert mcp.options["Pointer"]["annotations"].idempotentHint is False


def test_pointer_down_accepts_json_loc_and_defaults() -> None:
    desktop = FakeDesktop()
    _, tool = _registered(desktop)

    result = asyncio.run(tool(action="down", loc="[10, 20]"))

    assert result == (
        "Pressed left mouse button at (10,20); automatic release timeout is 30 seconds."
    )
    assert desktop.calls == [("down", (10, 20), "left", 30.0)]


def test_pointer_move_accepts_bounded_duration() -> None:
    desktop = FakeDesktop()
    _, tool = _registered(desktop)

    result = asyncio.run(tool(action=" MOVE ", loc=[30, 40], duration="0.25"))

    assert result == "Moved held left mouse button to (30,40) over 0.250 seconds."
    assert desktop.calls == [("move", (30, 40), 0.25)]


def test_pointer_up_asserts_optional_button() -> None:
    desktop = FakeDesktop()
    _, tool = _registered(desktop)

    result = asyncio.run(tool(action="up", button="right"))

    assert result == "Released right mouse button."
    assert desktop.calls == [("up", "right")]


def test_pointer_cancel_has_no_extra_arguments() -> None:
    desktop = FakeDesktop()
    _, tool = _registered(desktop)

    result = asyncio.run(tool(action="cancel"))

    assert result == "Released all mouse buttons; no tracked button was held."
    assert desktop.calls == [("cancel",)]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"action": "invalid"}, "action must be one of"),
        ({"action": "down"}, "loc is required"),
        ({"action": "down", "loc": [1, 2], "duration": 1}, "only supported.*move"),
        ({"action": "move", "loc": [1, 2], "button": "left"}, "only supported.*down"),
        ({"action": "move", "loc": [1, 2], "timeout": 1}, "only supported.*down"),
        ({"action": "up", "loc": [1, 2]}, "not supported.*up"),
        ({"action": "up", "duration": 1}, "not supported.*up"),
        ({"action": "cancel", "button": "left"}, "not supported.*cancel"),
        ({"action": "down", "loc": "not json"}, "JSON list"),
        ({"action": "down", "loc": [1, True]}, "integers"),
        ({"action": "down", "loc": [1, 2], "button": ""}, "button must be"),
        ({"action": "down", "loc": [1, 2], "button": "primary"}, "button must be"),
        ({"action": "down", "loc": [1, 2], "timeout": True}, "finite"),
        ({"action": "move", "loc": [1, 2], "duration": "nan"}, "finite"),
    ],
)
def test_pointer_rejects_invalid_combinations_before_desktop(
    kwargs: dict[str, object],
    message: str,
) -> None:
    desktop = FakeDesktop()
    _, tool = _registered(desktop)

    with pytest.raises(ValueError, match=message):
        asyncio.run(tool(**kwargs))

    assert desktop.calls == []
