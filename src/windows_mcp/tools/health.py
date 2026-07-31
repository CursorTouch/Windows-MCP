"""Health tool - minimal read-only MCP availability probe."""

import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastmcp import Context
from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics

_STARTED_AT = datetime.now(UTC)


def register(
    mcp: Any,
    *,
    get_desktop: Callable[[], Any],
    get_analytics: Callable[[], Any],
) -> None:
    """Register a safe first-call probe that does not invoke PowerShell or inspect user data."""

    @mcp.tool(
        name="Health",
        description=(
            "Safe read-only first call for Windows MCP GPT. Confirms that the MCP server is "
            "alive without running PowerShell, reading files, inspecting the screen, accessing "
            "the network, or changing Windows. Use this before broader diagnostic calls."
        ),
        annotations=ToolAnnotations(
            title="Windows MCP GPT Health",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Health-Tool")
    def health_tool(ctx: Context = None) -> dict[str, object]:
        now = datetime.now(UTC)
        return {
            "status": "ok",
            "server": "windows-mcp",
            "process_id": os.getpid(),
            "started_at": _STARTED_AT.isoformat(),
            "checked_at": now.isoformat(),
            "uptime_seconds": max(0, int((now - _STARTED_AT).total_seconds())),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "safe_probe": True,
        }
