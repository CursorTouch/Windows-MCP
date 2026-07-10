import asyncio
from collections.abc import Callable

import pytest

from windows_mcp.tools.input import register


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


class FakeDesktop:
    def __init__(self, *, fail_guard: bool = False) -> None:
        self.desktop_state = object()
        self.fail_guard = fail_guard
        self.guard_calls: list[dict[str, str | None]] = []
        self.action_calls: list[tuple[str, object]] = []

    def assert_foreground_target(
        self,
        expected_window_title: str | None = None,
        expected_process: str | None = None,
    ) -> None:
        self.guard_calls.append(
            {
                "expected_window_title": expected_window_title,
                "expected_process": expected_process,
            }
        )
        if self.fail_guard:
            raise ValueError("foreground target mismatch")

    def click(self, **kwargs: object) -> None:
        self.action_calls.append(("click", kwargs))

    def type(self, **kwargs: object) -> None:
        self.action_calls.append(("type", kwargs))

    def scroll(self, *args: object) -> None:
        self.action_calls.append(("scroll", args))

    def move(self, loc: list[int]) -> None:
        self.action_calls.append(("move", loc))

    def drag(self, loc: list[int]) -> None:
        self.action_calls.append(("drag", loc))

    def shortcut(self, shortcut: str) -> None:
        self.action_calls.append(("shortcut", shortcut))


def _tools(desktop: FakeDesktop) -> dict[str, Callable]:
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp.tools


def test_legacy_click_does_not_assert_target() -> None:
    desktop = FakeDesktop()

    result = asyncio.run(_tools(desktop)["Click"](loc=[10, 20]))

    assert result == "Single left clicked at (10,20)."
    assert desktop.guard_calls == []
    assert desktop.action_calls[0][0] == "click"


@pytest.mark.parametrize(
    ("tool_name", "kwargs", "expected_action"),
    [
        ("Click", {"loc": [10, 20]}, "click"),
        ("Type", {"text": "hello", "loc": [10, 20]}, "type"),
        ("Scroll", {"loc": [10, 20]}, "scroll"),
        ("Move", {"loc": [10, 20]}, "move"),
        ("Shortcut", {"shortcut": "ctrl+s"}, "shortcut"),
    ],
)
def test_guarded_input_tools_assert_target_before_input(
    tool_name: str,
    kwargs: dict[str, object],
    expected_action: str,
) -> None:
    desktop = FakeDesktop()
    kwargs.update(
        {
            "expected_window_title": "Notepad",
            "expected_process": "notepad.exe",
        }
    )

    asyncio.run(_tools(desktop)[tool_name](**kwargs))

    assert desktop.guard_calls == [
        {
            "expected_window_title": "Notepad",
            "expected_process": "notepad.exe",
        }
    ]
    assert desktop.action_calls[0][0] == expected_action


@pytest.mark.parametrize(
    ("tool_name", "kwargs"),
    [
        ("Click", {"loc": [10, 20]}),
        ("Type", {"text": "hello", "loc": [10, 20]}),
        ("Scroll", {"loc": [10, 20]}),
        ("Move", {"loc": [10, 20]}),
        ("Shortcut", {"shortcut": "ctrl+s"}),
    ],
)
def test_guarded_input_tools_fail_before_input_on_mismatch(
    tool_name: str,
    kwargs: dict[str, object],
) -> None:
    desktop = FakeDesktop(fail_guard=True)
    kwargs["expected_process"] = "notepad.exe"

    with pytest.raises(ValueError, match="foreground target mismatch"):
        asyncio.run(_tools(desktop)[tool_name](**kwargs))

    assert desktop.guard_calls == [
        {
            "expected_window_title": None,
            "expected_process": "notepad.exe",
        }
    ]
    assert desktop.action_calls == []
