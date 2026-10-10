"""FileSystem and MultiSelect must not read a boolean|string flag as false.

#440 routed clear/press_enter through desktop.utils.as_bool so a schema-valid
"yes" could no longer silently mean false. These two tools kept their own
inline comparison against the literal "true", so FileSystem(mode="write",
append="yes") truncated the file it was asked to append to.
"""

import asyncio
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from windows_mcp.tools.filesystem import register as register_filesystem
from windows_mcp.tools.multi import register as register_multi


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
