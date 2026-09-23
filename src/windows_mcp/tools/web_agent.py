"""MCP tool for goal-driven browser-agent execution."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import Context
from mcp.types import ToolAnnotations
from windows_mcp.agent.service import WebAgentService
from windows_mcp.infrastructure import with_analytics


def _run_report(
    url: str,
    goal: str,
    policy: str,
    max_steps: int,
    headless: bool | str,
    endpoint: str | None,
    dry_run: bool | str,
    timeout_ms: int,
) -> str:
    try:
        report = WebAgentService(
            policy=policy,
            max_steps=max_steps,
            headless=headless,
            endpoint=endpoint,
            dry_run=dry_run,
            timeout_ms=timeout_ms,
        ).run(url=url, goal=goal)
        return json.dumps(report, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return f"Error: {exc}"


def WebAgent(
    url: str,
    goal: str,
    policy: str = "auto",
    max_steps: int = 25,
    headless: bool | str = False,
    endpoint: str | None = None,
    dry_run: bool | str = False,
    timeout_ms: int = 30000,
    ctx: Context = None,
) -> str:
    """Run a goal-driven Jev-style browser agent and return a JSON report.

    The model never receives selectors or coordinates. It chooses only from
    elements observed in the current structured snapshot. Set ``dry_run=True``
    to exercise one decision without executing it.
    """

    return _run_report(
        url=url,
        goal=goal,
        policy=policy,
        max_steps=max_steps,
        headless=headless,
        endpoint=endpoint,
        dry_run=dry_run,
        timeout_ms=timeout_ms,
    )


def register(mcp: Any, *, get_desktop: Any, get_analytics: Any) -> None:
    """Register the ``WebAgent`` tool on an MCP server."""

    @mcp.tool(
        name="WebAgent",
        description=(
            "Goal-driven browser agent. Give a URL and a natural-language goal; "
            "it observes the page, chooses one validated typed operation per step, "
            "and executes it through indexed Playwright refs. Supports TypeSafe Jev, "
            "OpenRouter structured output, and a reserved local Laya backend. "
            "Use dry_run=true to preview one decision without executing actions."
        ),
        annotations=ToolAnnotations(
            title="WebAgent",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_analytics(get_analytics(), "WebAgent-Tool")
    def web_agent_tool(
        url: str,
        goal: str,
        policy: str = "auto",
        max_steps: int = 25,
        headless: bool | str = False,
        endpoint: str | None = None,
        dry_run: bool | str = False,
        timeout_ms: int = 30000,
        ctx: Context = None,
    ) -> str:
        """MCP wrapper around the shared WebAgent runner."""

        return _run_report(
            url=url,
            goal=goal,
            policy=policy,
            max_steps=max_steps,
            headless=headless,
            endpoint=endpoint,
            dry_run=dry_run,
            timeout_ms=timeout_ms,
        )


__all__ = ["WebAgent", "register"]
