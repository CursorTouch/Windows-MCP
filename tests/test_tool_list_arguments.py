"""Snapshot, Screenshot and App accept list arguments sent as JSON text.

MCP clients that strip the anyOf schema of optional parameters make the model
send a list as its JSON text: display="[0, 1]" instead of display=[0, 1]
(#272). loc, locs, labels, region and App's args already accept that form.
display, window_loc and window_size were still declared list-only, so FastMCP
rejected the call ("Input should be a valid list") before the tool ran. The
calls go through a real FastMCP server because its argument validation is
where they failed. The desktop is a fake that only records calls, and
Snapshot/Screenshot capture nothing.
"""

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.tools import app, snapshot


class FakeDesktop:
    def __init__(self) -> None:
        self.app_calls: list[tuple[object, ...]] = []

    def app(self, mode, name, window_loc, window_size) -> str:
        self.app_calls.append((mode, name, window_loc, window_size))
        return "app result"


@pytest.fixture
def desktop() -> FakeDesktop:
    return FakeDesktop()


@pytest.fixture
def captures(monkeypatch) -> list[dict[str, object]]:
    """Record what Snapshot/Screenshot would capture, without capturing."""
    calls: list[dict[str, object]] = []

    def capture_desktop_state(_desktop, **kwargs):
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(snapshot, "capture_desktop_state", capture_desktop_state)
    monkeypatch.setattr(snapshot, "build_snapshot_response", lambda *args, **kwargs: ["captured"])
    return calls


@pytest.fixture
def server(monkeypatch, desktop, captures) -> FastMCP:
    # register() rebinds these module globals, which other tests reach through
    # windows_mcp.__main__, so put them back afterwards.
    monkeypatch.setattr(snapshot, "state_tool", snapshot.state_tool)
    monkeypatch.setattr(snapshot, "screenshot_tool", snapshot.screenshot_tool)
    mcp = FastMCP("list-arguments")
    app.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    snapshot.register(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)
    return mcp


async def _call(server: FastMCP, tool: str, arguments: dict[str, object]):
    async with Client(server) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["Snapshot", "Screenshot"])
@pytest.mark.parametrize(
    ("display", "expected"),
    [("[0]", [0]), ("[0, 1]", [0, 1]), ([1], [1]), (None, None)],
)
async def test_display_accepts_json_text(server, captures, tool, display, expected):
    result = await _call(server, tool, {"display": display})

    assert not result.is_error, result.content[0].text
    assert [call["display"] for call in captures] == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["Snapshot", "Screenshot"])
@pytest.mark.parametrize("display", ["[0,", "0", '["0"]', "[true]"])
async def test_display_text_that_is_not_an_array_of_integers_is_reported(
    server, captures, tool, display
):
    result = await _call(server, tool, {"display": display})

    assert "display must be a JSON array of integers" in result.content[0].text
    assert captures == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("window_loc", "window_size"),
    [("[10, 20]", "[800, 600]"), ([10, 20], [800, 600])],
)
async def test_app_resize_accepts_json_text(server, desktop, window_loc, window_size):
    result = await _call(
        server,
        "App",
        {"mode": "resize", "name": "Notepad", "window_loc": window_loc, "window_size": window_size},
    )

    assert not result.is_error, result.content[0].text
    assert desktop.app_calls == [("resize", "Notepad", [10, 20], [800, 600])]


@pytest.mark.asyncio
@pytest.mark.parametrize("argument", ["window_loc", "window_size"])
@pytest.mark.parametrize("value", ["[10,", "10", '["10", "20"]'])
async def test_app_text_that_is_not_an_array_of_integers_is_reported(
    server, desktop, argument, value
):
    result = await _call(server, "App", {"mode": "resize", "name": "Notepad", argument: value})

    assert result.is_error
    assert f"{argument} must be a JSON array of integers" in result.content[0].text
    assert desktop.app_calls == []
