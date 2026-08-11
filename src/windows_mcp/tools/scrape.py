"""Scrape tool — fetch/scrape web page content."""

from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from fastmcp import Context


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="Scrape",
        description="Fetch/scrape web page content from a URL. Keywords: scrape, fetch, browse, web, URL, extract, download, read webpage. By default (use_dom=False), performs a lightweight HTTP request to the URL and returns a clean LLM-processed summary of the page to avoid context bloat. Provide query to focus extraction on specific information. Set use_dom=True to extract from the active browser tab's DOM instead (required when site blocks HTTP requests; supported in Chrome, Edge, and Firefox). Set use_sampling=False to get raw content without LLM processing.",
        annotations=ToolAnnotations(
            title="Scrape",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    @with_analytics(get_analytics(), "Scrape-Tool")
    async def scrape_tool(
        url: str,
        query: str | None = None,
        use_dom: bool | str = False,
        use_sampling: bool | str = True,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        use_dom = use_dom is True or (isinstance(use_dom, str) and use_dom.lower() == "true")
        use_sampling = use_sampling is True or (isinstance(use_sampling, str) and use_sampling.lower() == "true")

        if not use_dom:
            content = desktop.scrape(url)
        else:
            desktop_state = desktop.get_state(use_vision=False, use_dom=True)
            tree_state = desktop_state.tree_state
            if not tree_state.dom_node:
                return f"No DOM information found. Please open {url} in browser first."
            dom_node = tree_state.dom_node
            vertical_scroll_percent = getattr(dom_node, 'vertical_scroll_percent', 0)
            content = "\n".join([node.text for node in tree_state.dom_informative_nodes])
            header_status = "Reached top" if vertical_scroll_percent <= 0 else "Scroll up to see more"
            footer_status = (
                "Reached bottom" if vertical_scroll_percent >= 100 else "Scroll down to see more"
            )
            content = f"{header_status}\n{content}\n{footer_status}"

        if use_sampling and ctx is not None:
            try:
                focus = f" Focus specifically on: {query}." if query else ""
                result = await ctx.sample(
                    messages=f"Raw scraped content from {url}:\n\n{content}",
                    system_prompt=(
                        "You are a web content extractor. Given raw webpage content, extract and present "
                        "only the meaningful information in clean, concise prose or structured format. "
                        "Strip out navigation menus, cookie banners, ads, footer links, and all other "
                        f"boilerplate. Preserve important data, facts, and structure.{focus}"
                    ),
                    max_tokens=2048,
                )
                return f"URL: {url}\nContent:\n{result.text}"
            except Exception:
                pass  # Fall through to raw content if sampling not supported by client

        return f"URL: {url}\nContent:\n{content}"
