"""manifest.json must advertise exactly the tools the server registers."""

import json
from collections.abc import Callable
from pathlib import Path

from windows_mcp.tools import register_all

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest.json"


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


def test_manifest_lists_every_registered_tool() -> None:
    mcp = FakeMCP()
    register_all(mcp, get_desktop=lambda: None, get_analytics=lambda: None)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    listed = [tool["name"] for tool in manifest["tools"]]

    assert sorted(listed) == sorted(mcp.tools)
