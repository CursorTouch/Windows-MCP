"""Click and Scroll must say what they did.

Scroll at the mouse position (loc=None) answered with an empty string, and
Click answered "None left clicked" for any count other than 0, 1 or 2.
"""

import asyncio
from collections.abc import Callable
from unittest.mock import MagicMock

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


@pytest.fixture
def desktop() -> MagicMock:
    fake = MagicMock()
    fake.scroll.return_value = None
    # A click that landed everywhere it pointed: no warning to pass on.
    fake.click.return_value = ""
    return fake


def _call(desktop: MagicMock, tool: str, **kwargs: object) -> str:
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return asyncio.run(mcp.tools[tool](**kwargs))


def test_scroll_at_the_mouse_position_reports_the_scroll(desktop: MagicMock) -> None:
    result = _call(desktop, "Scroll", direction="up", wheel_times=3)

    desktop.scroll.assert_called_once_with(None, "vertical", "up", 3)
    assert result == "Scrolled vertical up by 3 wheel times at the current mouse position."


def test_scroll_at_a_point_names_the_point(desktop: MagicMock) -> None:
    result = _call(desktop, "Scroll", loc=[10, 20])

    assert result == "Scrolled vertical down by 1 wheel times at (10,20)."


@pytest.mark.parametrize(
    ("clicks", "count"), [(0, "Hover"), (2, "Double"), (3, "Triple"), (4, "4x")]
)
def test_click_names_the_click_count(desktop: MagicMock, clicks: int, count: str) -> None:
    result = _call(desktop, "Click", loc=[10, 20], clicks=clicks)

    desktop.click.assert_called_once_with(loc=[10, 20], button="left", clicks=clicks)
    assert result == f"{count} left clicked at (10,20)."


def test_negative_click_count_clicks_nothing(desktop: MagicMock) -> None:
    with pytest.raises(ValueError, match="clicks must be 0 or more"):
        _call(desktop, "Click", loc=[10, 20], clicks=-1)

    desktop.click.assert_not_called()
