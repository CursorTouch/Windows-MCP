"""Tools must not read a boolean|string flag they cannot parse as false.

#440 routed clear/press_enter through desktop.utils.as_bool so a schema-valid
"yes" could no longer silently mean false. These two tools kept their own
inline comparison against the literal "true", so FileSystem(mode="write",
append="yes") truncated the file it was asked to append to.
"""

import asyncio
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from windows_mcp import process as process_module
from windows_mcp.tools import snapshot as snapshot_module
from windows_mcp.tools.filesystem import register as register_filesystem
from windows_mcp.tools.multi import register as register_multi
from windows_mcp.tools.process import register as register_process
from windows_mcp.tools.scrape import register as register_scrape


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


def _tools(register) -> dict[str, Callable]:
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: MagicMock(), get_analytics=lambda: None)
    return mcp.tools


def test_unparsable_append_leaves_the_file_alone(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("first", encoding="utf-8")
    tool = _tools(register_filesystem)["FileSystem"]

    with pytest.raises(ValueError, match="append must be true or false"):
        asyncio.run(tool(mode="write", path=str(f), content=" second", append="yes"))

    assert f.read_text(encoding="utf-8") == "first"


def test_padded_true_appends(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("first", encoding="utf-8")
    tool = _tools(register_filesystem)["FileSystem"]

    result = asyncio.run(tool(mode="write", path=str(f), content=" second", append=" TRUE "))

    assert "Appended to" in result
    assert f.read_text(encoding="utf-8") == "first second"


def test_unparsable_press_ctrl_selects_nothing():
    desktop = MagicMock()
    desktop.desktop_state = object()
    mcp = FakeMCP()
    register_multi(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)

    with pytest.raises(ValueError, match="press_ctrl must be true or false"):
        asyncio.run(mcp.tools["MultiSelect"](locs=[[10, 10], [40, 40]], press_ctrl="yes"))

    desktop.multi_select.assert_not_called()


# Snapshot, Screenshot, Scrape and Process kept the same inline rule.


@pytest.fixture
def snapshot_captures(monkeypatch):
    """Record what Snapshot/Screenshot ask capture_desktop_state for, and stop there."""
    calls = []

    def capture(desktop, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("captured")

    monkeypatch.setattr(snapshot_module, "capture_desktop_state", capture)
    return calls


def test_snapshot_padded_true_takes_the_screenshot(snapshot_captures):
    asyncio.run(_tools(snapshot_module.register)["Snapshot"](use_vision=" TRUE "))

    assert snapshot_captures[0]["use_vision"] is True


@pytest.mark.parametrize(
    ("tool", "flag"), [("Snapshot", "use_ui_tree"), ("Screenshot", "use_annotation")]
)
def test_unparsable_snapshot_flag_captures_nothing(snapshot_captures, tool, flag):
    result = asyncio.run(_tools(snapshot_module.register)[tool](**{flag: "yes"}))

    assert f"{flag} must be true or false" in result[0]
    assert snapshot_captures == []


def test_padded_true_scrapes_the_browser_dom():
    desktop = MagicMock()
    desktop.get_state.return_value.tree_state.dom_node = None
    mcp = FakeMCP()
    register_scrape(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)

    result = asyncio.run(mcp.tools["Scrape"](url="https://example.com", use_dom=" TRUE "))

    assert result.startswith("No DOM information found")
    desktop.scrape.assert_not_called()


def test_unparsable_use_dom_fetches_nothing():
    desktop = MagicMock()
    mcp = FakeMCP()
    register_scrape(mcp, get_desktop=lambda: desktop, get_analytics=lambda: None)

    with pytest.raises(ValueError, match="use_dom must be true or false"):
        asyncio.run(mcp.tools["Scrape"](url="https://example.com", use_dom="yes"))

    desktop.scrape.assert_not_called()
    desktop.get_state.assert_not_called()


def test_process_force_is_parsed_strictly(monkeypatch):
    kills = []
    monkeypatch.setattr(
        process_module, "kill_process", lambda **kwargs: kills.append(kwargs) or "killed"
    )
    tool = _tools(register_process)["Process"]

    with pytest.raises(ValueError, match="force must be true or false"):
        asyncio.run(tool(mode="kill", pid=1234, force="yes"))
    assert kills == []

    asyncio.run(tool(mode="kill", pid=1234, force=" TRUE "))
    assert kills == [{"name": None, "pid": 1234, "force": True}]
