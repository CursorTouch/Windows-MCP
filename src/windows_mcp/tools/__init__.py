"""tools subpackage — registers all MCP tool definitions on a FastMCP instance."""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

# Core tools shipped with the server. An import failure here is fatal on purpose:
# these have no third-party dependency beyond the project's own requirements.
_CORE_MODULES: tuple[str, ...] = (
    "app",
    "display",
    "shell",
    "filesystem",
    "snapshot",
    "input",
    "scrape",
    "multi",
    "clipboard",
    "process",
    "notification",
    "registry",
)

# Capability packs. Each may depend on third-party libraries that are not
# installed in every deployment, so a failed import disables only that pack and
# logs a warning instead of taking the whole server down.
_OPTIONAL_MODULES: tuple[str, ...] = (
    "ocr",
    "web",
    "web_agent",
    "act",
    "inputx",
    "systemx",
    "net",
    "office",
)


def _load(name: str, *, optional: bool):
    """Import ``windows_mcp.tools.<name>`` and return the module or ``None``."""
    try:
        return importlib.import_module(f"windows_mcp.tools.{name}")
    except Exception as exc:  # noqa: BLE001 - report and continue serving
        if not optional:
            raise
        logger.warning("Tool pack %r unavailable, skipping: %s", name, exc)
        return None


def register_all(mcp, *, get_desktop, get_analytics) -> None:
    """Register every tool module on *mcp*.

    *get_desktop* and *get_analytics* are zero-arg callables that return the
    current ``Desktop`` and ``PostHogAnalytics`` instances (resolved lazily so
    that tools can be registered before ``lifespan`` initializes the singletons).
    """
    modules = [_load(name, optional=False) for name in _CORE_MODULES]
    modules += [
        mod
        for mod in (_load(name, optional=True) for name in _OPTIONAL_MODULES)
        if mod is not None
    ]
    for mod in modules:
        mod.register(mcp, get_desktop=get_desktop, get_analytics=get_analytics)
