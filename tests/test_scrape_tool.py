"""Scrape(use_dom=True) must report the page's real scroll position."""

import asyncio
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from windows_mcp.tools import scrape as scrape_tool_module
from windows_mcp.tree.views import BoundingBox, Center, ScrollElementNode, TextElementNode


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


class FakeDesktop:
    def __init__(self, vertical_scroll_percent: float) -> None:
        self.dom_node = ScrollElementNode(
            name="DOM",
            control_type="DocumentControl",
            window_name="DOM",
            bounding_box=BoundingBox(left=0, top=0, right=800, bottom=600, width=800, height=600),
            center=Center(x=400, y=300),
            metadata={
                "vertical_scrollable": True,
                "vertical_scroll_percent": vertical_scroll_percent,
            },
        )

    def get_state(self, use_vision: bool = False, use_dom: bool = False) -> SimpleNamespace:
        tree_state = SimpleNamespace(
            dom_node=self.dom_node,
            dom_informative_nodes=[TextElementNode(text="Page text")],
        )
        return SimpleNamespace(tree_state=tree_state)


@pytest.mark.parametrize(
    ("vertical_scroll_percent", "header", "footer"),
    [
        (0, "Reached top", "Scroll down to see more"),
        (42.5, "Scroll up to see more", "Scroll down to see more"),
        (100, "Scroll up to see more", "Reached bottom"),
    ],
)
def test_dom_scrape_reports_scroll_position(
    vertical_scroll_percent: float, header: str, footer: str
) -> None:
    mcp = FakeMCP()
    desktop = FakeDesktop(vertical_scroll_percent)
    scrape_tool_module.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)

    result = asyncio.run(
        mcp.tools["Scrape"](url="https://example.com", use_dom=True, use_sampling=False)
    )

    assert result == f"URL: https://example.com\nContent:\n{header}\nPage text\n{footer}"
