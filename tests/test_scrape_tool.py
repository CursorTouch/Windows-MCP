"""Scrape tool behaviour.

Scrape(use_dom=True) must report the page's real scroll position, and
Scrape(use_dom=False) must fetch the page off the event loop: a slow site must
not hold up every other request to the server while it loads.
"""

import asyncio
import threading
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from windows_mcp.tools import scrape as scrape_tool_module
from windows_mcp.tree.service import Tree
from windows_mcp.tree.views import BoundingBox, Center, ScrollElementNode, TextElementNode


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


DOM_BOX = BoundingBox(left=0, top=0, right=800, bottom=600, width=800, height=600)


class FakeDesktop:
    def __init__(
        self, vertical_scroll_percent: float, vertical_scrollable: bool | None = True
    ) -> None:
        self.dom_node = ScrollElementNode(
            name="DOM",
            control_type="DocumentControl",
            window_name="DOM",
            bounding_box=DOM_BOX,
            center=Center(x=400, y=300),
            metadata={
                "vertical_scrollable": vertical_scrollable,
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


def _dom_scrape(desktop: FakeDesktop) -> str:
    mcp = FakeMCP()
    scrape_tool_module.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return asyncio.run(
        mcp.tools["Scrape"](url="https://example.com", use_dom=True, use_sampling=False)
    )


def test_dom_scrape_of_a_page_that_does_not_scroll_reaches_both_ends() -> None:
    # UIA reports VerticallyScrollable=False and the percent as 0 when the page fits.
    result = _dom_scrape(FakeDesktop(0, vertical_scrollable=False))

    assert result == "URL: https://example.com\nContent:\nReached top\nPage text\nReached bottom"


def test_firefox_dom_scrape_still_offers_to_scroll() -> None:
    # The IA2 walk has no scroll pattern, so it cannot tell whether more text follows.
    tree = Tree.__new__(Tree)
    tree.screen_box = DOM_BOX

    def walk(windows_handles, active_window_flag, use_dom):
        tree.dom_is_ia2 = True
        tree.dom_bounding_box = DOM_BOX
        return [], [], [], [], []

    tree.get_window_wise_nodes = walk
    desktop = FakeDesktop(0)
    desktop.dom_node = tree.get_state(None, [1], use_dom=True).dom_node

    result = _dom_scrape(desktop)

    assert result.endswith("Reached top\nPage text\nScroll down to see more")


class SlowFetchDesktop:
    """Desktop stand-in whose fetch finishes only if the event loop keeps running."""

    def __init__(self) -> None:
        self.loop_ran = threading.Event()

    def scrape(self, url: str) -> str:
        # Only the event loop sets loop_ran. If scrape() runs on the loop thread,
        # nothing can set it before the wait times out.
        if self.loop_ran.wait(timeout=5):
            return "page text"
        return "event loop was blocked"


async def test_http_scrape_does_not_block_the_event_loop() -> None:
    mcp = FakeMCP()
    desktop = SlowFetchDesktop()
    scrape_tool_module.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)

    scrape_call = asyncio.create_task(
        mcp.tools["Scrape"](url="https://example.com", use_sampling=False)
    )
    await asyncio.sleep(0.05)  # stands in for another request served meanwhile
    desktop.loop_ran.set()

    assert await scrape_call == "URL: https://example.com\nContent:\npage text"
