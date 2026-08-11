import asyncio
from collections.abc import Callable

import pytest

from windows_mcp.tools.input import register


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}
        self.tool_options: dict[str, dict[str, object]] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        self.tool_options[name] = kwargs

        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


class FakeDesktop:
    def __init__(self) -> None:
        self.desktop_state = object()
        self.move_calls: list[list[int]] = []
        self.drag_calls: list[dict[str, object]] = []

    def move(self, loc: list[int]) -> None:
        self.move_calls.append(loc)

    def drag(self, loc: list[int], **kwargs: object) -> dict[str, object]:
        self.drag_calls.append({"loc": loc, **kwargs})
        return {
            "start": kwargs.get("from_loc") or [1, 2],
            "end": loc,
            "duration": 0.25 if kwargs.get("duration") is not None else None,
        }


def _tools(desktop: FakeDesktop) -> dict[str, Callable]:
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools


def test_move_tool_preserves_legacy_move_behavior() -> None:
    desktop = FakeDesktop()
    result = asyncio.run(_tools(desktop)["Move"](loc=[10, 20]))

    assert result == "Moved the mouse pointer to (10,20)."
    assert desktop.move_calls == [[10, 20]]
    assert desktop.drag_calls == []


def test_move_tool_rejects_invalid_drag_boolean_before_input() -> None:
    desktop = FakeDesktop()

    with pytest.raises(ValueError, match="drag must be true or false"):
        asyncio.run(_tools(desktop)["Move"](loc=[10, 20], drag="tru"))

    assert desktop.move_calls == []
    assert desktop.drag_calls == []


def test_move_tool_annotations_cover_drag_side_effects() -> None:
    desktop = FakeDesktop()
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)

    annotations = mcp.tool_options["Move"]["annotations"]

    assert annotations.destructiveHint is True
    assert annotations.idempotentHint is False


def test_move_tool_accepts_explicit_drag_start_list() -> None:
    desktop = FakeDesktop()
    result = asyncio.run(
        _tools(desktop)["Move"](
            loc=[100, 200],
            drag=True,
            from_loc=[10, 20],
            duration=0.25,
        )
    )

    assert result == "Dragged from (10,20) to (100,200) over 0.250 seconds."
    assert desktop.drag_calls == [
        {
            "loc": [100, 200],
            "from_loc": [10, 20],
            "duration": 0.25,
        }
    ]


def test_move_tool_accepts_explicit_drag_start_json_string() -> None:
    desktop = FakeDesktop()
    result = asyncio.run(
        _tools(desktop)["Move"](
            loc="[100, 200]",
            drag="true",
            from_loc="[10, 20]",
        )
    )

    assert result == "Dragged from (10,20) to (100,200)."
    assert desktop.drag_calls[0]["from_loc"] == [10, 20]


def test_move_tool_rejects_invalid_from_loc() -> None:
    with pytest.raises(ValueError, match="from_loc"):
        asyncio.run(_tools(FakeDesktop())["Move"](loc=[100, 200], drag=True, from_loc=[10]))


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("from_loc", [True, 20]),
        ("from_loc", [10.5, 20]),
        ("loc", [100, False]),
        ("loc", [100, "20.5"]),
    ],
)
def test_move_tool_rejects_non_integer_drag_points_before_input(
    argument: str,
    value: list[object],
) -> None:
    desktop = FakeDesktop()
    kwargs: dict[str, object] = {
        "loc": [100, 200],
        "drag": True,
        "from_loc": [10, 20],
        argument: value,
    }

    with pytest.raises(ValueError, match=argument):
        asyncio.run(_tools(desktop)["Move"](**kwargs))

    assert desktop.drag_calls == []


def test_move_tool_normalizes_string_drag_coordinates() -> None:
    desktop = FakeDesktop()

    asyncio.run(
        _tools(desktop)["Move"](
            loc=["100", "200"],
            drag=True,
            from_loc=["10", "+20"],
        )
    )

    assert desktop.drag_calls[0]["loc"] == [100, 200]
    assert desktop.drag_calls[0]["from_loc"] == [10, 20]


def test_move_tool_rejects_drag_options_without_drag() -> None:
    with pytest.raises(ValueError, match="require drag=True"):
        asyncio.run(_tools(FakeDesktop())["Move"](loc=[100, 200], from_loc=[10, 20]))
