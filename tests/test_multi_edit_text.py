"""MultiEdit checks the text of every field before it sends any input.

The tool's schema leaves the items of each locs and labels entry untyped, so a
client can send a number as a field's text, as in [[450, 320, 30]]. Only
Desktop.type() looked at the text, after it had clicked the field and cleared it:
the call then failed with "TypeError: argument of type 'int' is not a container or
iterable", and the fields before it had already been typed.
"""

import asyncio

import pytest

from windows_mcp.desktop import service
from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import DesktopState
from windows_mcp.tools.multi import register
from windows_mcp.tree.views import BoundingBox, Center, TreeElementNode, TreeState

SINGLE_MONITOR = (0, 0, 1920, 1080)
REFUSED = r"Text for the field at \(100,240\) must be a string"


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the clicks and keys MultiEdit sends instead of sending them."""
    events: list[str] = []
    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    monkeypatch.setattr(service.uia, "GetVirtualScreenRect", lambda: SINGLE_MONITOR)
    monkeypatch.setattr(
        service.uia, "Click", lambda x, y, *args, **kwargs: events.append(f"click {x},{y}")
    )
    monkeypatch.setattr(service.uia, "SendKeys", lambda keys, *args, **kwargs: events.append(keys))
    # No focused element: the clear sends its keys and nothing is read back.
    monkeypatch.setattr(service.uia, "GetFocusedControl", lambda: None)
    return events


def field(name: str, x: int, y: int) -> TreeElementNode:
    return TreeElementNode(
        bounding_box=BoundingBox(
            left=x - 50, top=y - 10, right=x + 50, bottom=y + 10, width=100, height=20
        ),
        center=Center(x=x, y=y),
        name=name,
        control_type="Edit",
        window_name="Order form",
    )


def make_desktop() -> Desktop:
    """A desktop whose last Snapshot found two fields: label 0 and label 1."""
    desktop = Desktop.__new__(Desktop)
    desktop.desktop_state = DesktopState(
        active_desktop={"name": "Desktop 1"},
        all_desktops=[],
        active_window=None,
        windows=[],
        tree_state=TreeState(
            interactive_nodes=[field("Name", 100, 200), field("Quantity", 100, 240)]
        ),
    )
    return desktop


class FakeMCP:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *, name, **kwargs):
        def decorator(func):
            self.tools[name] = func
            return func

        return decorator


@pytest.mark.parametrize("text", [30, 4.5, True, None])
def test_text_that_is_not_a_string_is_refused_before_any_input(
    sent: list[str], text: object
) -> None:
    with pytest.raises(ValueError, match=REFUSED):
        make_desktop().multi_edit([[100, 200, "Ann"], [100, 240, text]])

    assert sent == []


def test_string_texts_are_typed_into_each_field(sent: list[str]) -> None:
    make_desktop().multi_edit([[100, 200, "Ann"], [100, 240, "30"]])

    clear = ["{Home}", "{Shift}{End}", "{Back}"]
    assert sent == ["click 100,200", *clear, "A", "n", "n", "click 100,240", *clear, "3", "0"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"locs": [[100, 200, "Ann"], [100, 240, 30]]},
        {"locs": '[[100, 200, "Ann"], [100, 240, 30]]'},
        {"labels": [[0, "Ann"], [1, 30]]},
    ],
    ids=["locs", "locs-json-string", "labels"],
)
def test_multiedit_tool_refuses_a_number_before_any_input(
    sent: list[str], arguments: dict[str, object]
) -> None:
    mcp = FakeMCP()
    register(mcp, get_desktop=make_desktop, get_analytics=lambda: None)

    with pytest.raises(ValueError, match=REFUSED):
        asyncio.run(mcp.tools["MultiEdit"](**arguments))

    assert sent == []
