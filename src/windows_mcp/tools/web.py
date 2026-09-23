"""Web tool - deterministic Playwright/CDP browser automation."""

import json
from typing import Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from windows_mcp.web import get_web_service

WebMode = Literal[
    "connect",
    "launch",
    "close",
    "status",
    "snapshot",
    "click",
    "type",
    "select",
    "scroll",
    "hover",
    "press",
    "eval",
    "cookies",
    "network",
    "wait",
    "screenshot",
    "extract",
]
WaitMode = Literal["selector", "text", "url", "networkidle", "timeout"]
ScrollDirection = Literal["up", "down", "left", "right", "top", "bottom"]


def _as_loc(value: Any) -> Any:
    """Coerce a JSON-stringified list/dict back to a Python value.

    MCP clients may strip union schemas and serialize structured arguments as
    strings. Plain strings are kept unchanged so selectors and values still
    work normally.
    """

    if value is None or isinstance(value, (list, dict, bool, int, float)):
        return value
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return value
    return parsed


def register(mcp, *, get_desktop, get_analytics):
    """Register the Web tool with an MCP server."""

    @mcp.tool(
        name="Web",
        description=(
            "Fast deterministic browser automation through Playwright/CDP. Keywords: browser, "
            "Chrome, web automation, CDP, Playwright, click, type, snapshot, screenshot, DOM, "
            "network, cookies. Modes: connect (default CDP endpoint 127.0.0.1:9222), launch, "
            "close, status, snapshot, click, type, select, scroll, hover, press, eval, cookies, "
            "network, wait, screenshot, extract. Use snapshot to get compact indexed refs, then "
            "pass ref to later actions. Prefer connect for the user's already logged-in browser."
        ),
        annotations=ToolAnnotations(
            title="Web",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Web-Tool")
    def web_tool(
        mode: WebMode = "connect",
        endpoint: str = "http://127.0.0.1:9222",
        headless: bool | str = False,
        url: str | None = None,
        ref: int | str | None = None,
        selector: str | None = None,
        selectors: list[str] | str | None = None,
        text: str | None = None,
        value: Any = None,
        key: str | None = None,
        script: str | None = None,
        action: str = "get",
        payload: Any = None,
        wait_for: WaitMode | None = None,
        timeout_ms: int | str = 30000,
        amount: int | str = 600,
        direction: ScrollDirection = "down",
        scroll_to: ScrollDirection | None = None,
        full_page: bool | str = True,
        path: str | None = None,
        attributes: list[str] | str | None = None,
        fields: list[str] | str | None = None,
        clear: bool | str = True,
        append: bool | str = False,
        press_enter: bool | str = False,
        max_elements: int | str = 150,
        limit: int | str = 100,
        tab_index: int | str | None = None,
        ctx: Context = None,
    ) -> str:
        """Dispatch one deterministic browser action and return text output."""

        try:
            web = get_web_service()
            if mode == "connect":
                return web.connect(endpoint=endpoint, timeout_ms=timeout_ms)
            if mode == "launch":
                return web.launch(headless=headless, url=url, timeout_ms=timeout_ms)
            if mode == "close":
                return web.close()
            if mode == "status":
                return web.status(tab_index=tab_index)
            if mode == "snapshot":
                return web.snapshot(max_elements=max_elements)
            if mode == "click":
                return web.click(ref=ref, selector=selector, timeout_ms=timeout_ms)
            if mode == "type":
                if value is None:
                    return "Error: value is required for mode='type'."
                return web.type_text(
                    value=str(value),
                    ref=ref,
                    selector=selector,
                    clear=clear,
                    append=append,
                    press_enter=press_enter,
                    timeout_ms=timeout_ms,
                )
            if mode == "select":
                return web.select(
                    value=_as_loc(value),
                    text=text,
                    ref=ref,
                    selector=selector,
                    timeout_ms=timeout_ms,
                )
            if mode == "scroll":
                return web.scroll(
                    direction=direction,
                    amount=amount,
                    ref=ref,
                    selector=selector,
                    scroll_to=scroll_to,
                    timeout_ms=timeout_ms,
                )
            if mode == "hover":
                return web.hover(ref=ref, selector=selector, timeout_ms=timeout_ms)
            if mode == "press":
                if key is None:
                    return "Error: key is required for mode='press'."
                return web.press(
                    key=key,
                    ref=ref,
                    selector=selector,
                    timeout_ms=timeout_ms,
                )
            if mode == "eval":
                if script is None:
                    return "Error: script is required for mode='eval'."
                return web.evaluate(script=script)
            if mode == "cookies":
                return web.cookies(action=action, payload=_as_loc(payload))
            if mode == "network":
                return web.network(action=action, limit=limit)
            if mode == "wait":
                if wait_for is None:
                    return "Error: wait_for is required for mode='wait'."
                return web.wait(
                    wait_for=wait_for,
                    selector=selector,
                    text=text,
                    url=url,
                    timeout_ms=timeout_ms,
                )
            if mode == "screenshot":
                return web.screenshot(
                    path=path,
                    full_page=full_page,
                    ref=ref,
                    selector=selector,
                    timeout_ms=timeout_ms,
                )
            if mode == "extract":
                return web.extract(
                    selector=selector,
                    selectors=_as_loc(selectors),
                    fields=_as_loc(fields),
                    attributes=_as_loc(attributes),
                    max_elements=max_elements,
                )
            return f"Error: unknown mode {mode!r}."
        except Exception as exc:
            return f"Error: {exc}"
