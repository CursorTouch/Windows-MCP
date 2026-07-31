import asyncio
from collections.abc import Callable

from windows_mcp.tools import health as health_tool_module


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}
        self.options: dict[str, object] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        self.options[name] = kwargs

        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


def test_health_is_safe_read_only_probe() -> None:
    mcp = FakeMCP()
    health_tool_module.register(mcp, get_desktop=lambda: None, get_analytics=lambda: None)

    result = asyncio.run(mcp.tools["Health"]())
    annotations = mcp.options["Health"]["annotations"]

    assert result["status"] == "ok"
    assert result["server"] == "windows-mcp"
    assert result["safe_probe"] is True
    assert result["uptime_seconds"] >= 0
    assert annotations.readOnlyHint is True
    assert annotations.destructiveHint is False
    assert annotations.idempotentHint is True
    assert annotations.openWorldHint is False
