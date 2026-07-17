"""tools subpackage — registers all MCP tool definitions on a FastMCP instance."""

from windows_mcp.tools import (
    app,
    clipboard,
    display,
    filesystem,
    input,
    multi,
    notification,
    process,
    registry,
    scrape,
    shell,
    snapshot,
)

_MODULES = [
    app,
    display,
    shell,
    filesystem,
    snapshot,
    input,
    scrape,
    multi,
    clipboard,
    process,
    notification,
    registry,
]


def register_all(mcp, *, get_desktop, get_analytics):
    """Register every tool module on *mcp*.

    *get_desktop* and *get_analytics* are zero-arg callables that return the
    current ``Desktop`` and ``PostHogAnalytics`` instances (resolved lazily so
    that tools can be registered before ``lifespan`` initializes the singletons).
    """
    for mod in _MODULES:
        mod.register(mcp, get_desktop=get_desktop, get_analytics=get_analytics)
