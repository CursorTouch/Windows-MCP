from windows_mcp.analytics import PostHogAnalytics, with_analytics
from windows_mcp.desktop.service import Desktop, Size
from windows_mcp.watchdog.service import WatchDog
from contextlib import asynccontextmanager
from fastmcp.utilities.types import Image
from mcp.types import ToolAnnotations
from typing import Literal
from fastmcp import FastMCP, Context
from dotenv import load_dotenv
from textwrap import dedent
import pyautogui as pg
import asyncio
import click
import os

load_dotenv()

MAX_IMAGE_WIDTH, MAX_IMAGE_HEIGHT = 1920, 1080
pg.FAILSAFE = False
pg.PAUSE = 1.0

desktop: Desktop | None = None
watchdog: WatchDog | None = None
analytics: PostHogAnalytics | None = None
screen_size: Size | None = None

instructions = dedent("""
Windows MCP server provides tools to interact directly with the Windows desktop,
thus enabling to operate the desktop on the user's behalf.
""")


@asynccontextmanager
async def lifespan(app: FastMCP):
    """Runs initialization code before the server starts and cleanup code after it shuts down."""
    global desktop, watchdog, analytics, screen_size

    # Initialize components here instead of at module level
    if os.getenv("ANONYMIZED_TELEMETRY", "true").lower() != "false":
        analytics = PostHogAnalytics()
    desktop = Desktop()
    watchdog = WatchDog()
    screen_size = desktop.get_screen_size()
    watchdog.set_focus_callback(desktop.tree._on_focus_change)

    try:
        watchdog.start()
        await asyncio.sleep(1)  # Simulate startup latency
        yield
    finally:
        if watchdog:
            watchdog.stop()
        if analytics:
            await analytics.close()


mcp = FastMCP(name="windows-mcp", instructions=instructions, lifespan=lifespan)


def _to_physical(loc: list[int], coordinate_system: str) -> list[int]:
    """Convert coordinates to physical space if needed.

    Args:
        loc: [x, y] coordinates.
        coordinate_system: "physical" (no conversion) or "logical" (multiply by DPI scale).

    Returns:
        [x, y] in physical coordinates ready for pyautogui.
    """
    if coordinate_system == "logical":
        scale = desktop.get_dpi_scaling()
        return [int(loc[0] * scale), int(loc[1] * scale)]
    return loc


@mcp.tool(
    name="App",
    description="Manages Windows applications with three modes: 'launch' (opens the prescibed application), 'resize' (adjusts active window size/position), 'switch' (brings specific window into focus).",
    annotations=ToolAnnotations(
        title="App",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "App-Tool")
def app_tool(
    mode: Literal["launch", "resize", "switch"],
    name: str | None = None,
    window_loc: list[int] | None = None,
    window_size: list[int] | None = None,
    ctx: Context = None,
):
    return desktop.app(mode, name, window_loc, window_size)


@mcp.tool(
    name="Shell",
    description="A comprehensive system tool for executing any PowerShell commands. Use it to navigate the file system, manage files and processes, and execute system-level operations. Capable of accessing web content (e.g., via Invoke-WebRequest), interacting with network resources, and performing complex administrative tasks. This tool provides full access to the underlying operating system capabilities, making it the primary interface for system automation, scripting, and deep system interaction.",
    annotations=ToolAnnotations(
        title="Shell",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@with_analytics(analytics, "Powershell-Tool")
def powershell_tool(command: str, timeout: int = 30, ctx: Context = None) -> str:
    try:
        response, status_code = desktop.execute_command(command, timeout)
        return f"Response: {response}\nStatus Code: {status_code}"
    except Exception as e:
        return f"Error executing command: {str(e)}\nStatus Code: 1"


@mcp.tool(
    name="Snapshot",
    description="Captures complete desktop state including: system language, focused/opened windows, interactive elements (buttons, text fields, links, menus with coordinates), and scrollable areas. Set use_vision=True to include screenshot. Set use_dom=True for browser content to get web page elements instead of browser UI. Always call this first to understand the current desktop state before taking actions.",
    annotations=ToolAnnotations(
        title="Snapshot",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "State-Tool")
def state_tool(use_vision: bool | str = False, use_dom: bool | str = False, ctx: Context = None):
    try:
        use_vision = use_vision is True or (
            isinstance(use_vision, str) and use_vision.lower() == "true"
        )
        use_dom = use_dom is True or (isinstance(use_dom, str) and use_dom.lower() == "true")

        # Calculate scale factor to cap resolution at 1080p (1920x1080)
        scale_width = (
            MAX_IMAGE_WIDTH / screen_size.width if screen_size.width > MAX_IMAGE_WIDTH else 1.0
        )
        scale_height = (
            MAX_IMAGE_HEIGHT / screen_size.height if screen_size.height > MAX_IMAGE_HEIGHT else 1.0
        )
        scale = min(
            scale_width, scale_height
        )  # Use the smaller scale to ensure both dimensions fit

        desktop_state = desktop.get_state(
            use_vision=use_vision, use_dom=use_dom, as_bytes=True, scale=scale
        )
        interactive_elements = desktop_state.tree_state.interactive_elements_to_string()
        scrollable_elements = desktop_state.tree_state.scrollable_elements_to_string()
        windows = desktop_state.windows_to_string()
        active_window = desktop_state.active_window_to_string()
        active_desktop = desktop_state.active_desktop_to_string()
        all_desktops = desktop_state.desktops_to_string()
    except Exception as e:
        return [f"Error capturing desktop state: {str(e)}. Please try again."]
    return [
        dedent(f"""
    Active Desktop:
    {active_desktop}

    All Desktops:
    {all_desktops}

    Focused Window:
    {active_window}

    Opened Windows:
    {windows}

    List of Interactive Elements:
    {interactive_elements or "No interactive elements found."}

    List of Scrollable Elements:
    {scrollable_elements or "No scrollable elements found."}
    """)
    ] + ([Image(data=desktop_state.screenshot, format="png")] if use_vision else [])


@mcp.tool(
    name="Click",
    description=(
        "Performs mouse clicks at specified coordinates [x, y]. "
        "Supports button types: 'left' for selection/activation, 'right' for context menus, 'middle'. "
        "Supports clicks: 0=hover only (no click), 1=single click (select/focus), 2=double click (open/activate). "
        "Set coordinate_system='logical' to auto-convert from logical (DPI-scaled) coordinates to physical. "
        "Default is 'physical' (no conversion)."
    ),
    annotations=ToolAnnotations(
        title="Click",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Click-Tool")
def click_tool(
    loc: list[int],
    button: Literal["left", "right", "middle"] = "left",
    clicks: int = 1,
    coordinate_system: Literal["physical", "logical"] = "physical",
    ctx: Context = None,
) -> str:
    if len(loc) != 2:
        raise ValueError("Location must be a list of exactly 2 integers [x, y]")
    loc = _to_physical(loc, coordinate_system)
    x, y = loc[0], loc[1]
    desktop.click(loc=loc, button=button, clicks=clicks)
    num_clicks = {0: "Hover", 1: "Single", 2: "Double"}
    return f"{num_clicks.get(clicks)} {button} clicked at ({x},{y})."


@mcp.tool(
    name="Type",
    description=(
        "Types text at specified coordinates [x, y]. "
        "Set clear=True to clear existing text first, False to append. "
        "Set press_enter=True to submit after typing. "
        "Set caret_position to 'start' (beginning), 'end' (end), or 'idle' (default). "
        "Set coordinate_system='logical' to auto-convert from logical (DPI-scaled) coordinates to physical. "
        "Default is 'physical' (no conversion)."
    ),
    annotations=ToolAnnotations(
        title="Type",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Type-Tool")
def type_tool(
    loc: list[int],
    text: str,
    clear: bool | str = False,
    caret_position: Literal["start", "idle", "end"] = "idle",
    press_enter: bool | str = False,
    coordinate_system: Literal["physical", "logical"] = "physical",
    ctx: Context = None,
) -> str:
    if len(loc) != 2:
        raise ValueError("Location must be a list of exactly 2 integers [x, y]")
    loc = _to_physical(loc, coordinate_system)
    x, y = loc[0], loc[1]
    desktop.type(
        loc=loc,
        text=text,
        caret_position=caret_position,
        clear=clear,
        press_enter=press_enter,
    )
    return f"Typed {text} at ({x},{y})."


@mcp.tool(
    name="Scroll",
    description=(
        "Scrolls at coordinates [x, y] or current mouse position if loc=None. "
        "Type: vertical (default) or horizontal. "
        "Direction: up/down for vertical, left/right for horizontal. "
        "wheel_times controls amount (1 wheel ≈ 3-5 lines). "
        "Use for navigating long content, lists, and web pages. "
        "Set coordinate_system='logical' to auto-convert from logical (DPI-scaled) coordinates to physical. "
        "Default is 'physical' (no conversion)."
    ),
    annotations=ToolAnnotations(
        title="Scroll",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Scroll-Tool")
def scroll_tool(
    loc: list[int] = None,
    type: Literal["horizontal", "vertical"] = "vertical",
    direction: Literal["up", "down", "left", "right"] = "down",
    wheel_times: int = 1,
    coordinate_system: Literal["physical", "logical"] = "physical",
    ctx: Context = None,
) -> str:
    if loc and len(loc) != 2:
        raise ValueError("Location must be a list of exactly 2 integers [x, y]")
    if loc:
        loc = _to_physical(loc, coordinate_system)
    response = desktop.scroll(loc, type, direction, wheel_times)
    if response:
        return response
    return (
        f"Scrolled {type} {direction} by {wheel_times} wheel times" + f" at ({loc[0]},{loc[1]})."
        if loc
        else ""
    )


@mcp.tool(
    name="Move",
    description=(
        "Moves mouse cursor to coordinates [x, y]. "
        "Set drag=True to perform a drag-and-drop operation from the current mouse position "
        "to the target coordinates. Default (drag=False) is a simple cursor move (hover). "
        "Set coordinate_system='logical' to auto-convert from logical (DPI-scaled) coordinates to physical. "
        "Default is 'physical' (no conversion)."
    ),
    annotations=ToolAnnotations(
        title="Move",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Move-Tool")
def move_tool(
    loc: list[int],
    drag: bool | str = False,
    coordinate_system: Literal["physical", "logical"] = "physical",
    ctx: Context = None,
) -> str:
    drag = drag is True or (isinstance(drag, str) and drag.lower() == "true")
    if len(loc) != 2:
        raise ValueError("loc must be a list of exactly 2 integers [x, y]")
    loc = _to_physical(loc, coordinate_system)
    x, y = loc[0], loc[1]
    if drag:
        desktop.drag(loc)
        return f"Dragged to ({x},{y})."
    else:
        desktop.move(loc)
        return f"Moved the mouse pointer to ({x},{y})."


@mcp.tool(
    name="Shortcut",
    description='Executes keyboard shortcuts using key combinations separated by +. Examples: "ctrl+c" (copy), "ctrl+v" (paste), "alt+tab" (switch apps), "win+r" (Run dialog), "win" (Start menu), "ctrl+shift+esc" (Task Manager). Use for quick actions and system commands.',
    annotations=ToolAnnotations(
        title="Shortcut",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Shortcut-Tool")
def shortcut_tool(shortcut: str, ctx: Context = None):
    desktop.shortcut(shortcut)
    return f"Pressed {shortcut}."


@mcp.tool(
    name="Wait",
    description="Pauses execution for specified duration in seconds. Use when waiting for: applications to launch/load, UI animations to complete, page content to render, dialogs to appear, or between rapid actions. Helps ensure UI is ready before next interaction.",
    annotations=ToolAnnotations(
        title="Wait",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Wait-Tool")
def wait_tool(duration: int, ctx: Context = None) -> str:
    pg.sleep(duration)
    return f"Waited for {duration} seconds."


@mcp.tool(
    name="Scrape",
    description="Fetch content from a URL or the active browser tab. By default (use_dom=False), performs a lightweight HTTP request to the URL and returns markdown content of complete webpage. Note: Some websites may block automated HTTP requests. If this fails, open the page in a browser and retry with use_dom=True to extract visible text from the active tab's DOM within the viewport using the accessibility tree data.",
    annotations=ToolAnnotations(
        title="Scrape",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@with_analytics(analytics, "Scrape-Tool")
def scrape_tool(url: str, use_dom: bool | str = False, ctx: Context = None) -> str:
    use_dom = use_dom is True or (isinstance(use_dom, str) and use_dom.lower() == "true")
    if not use_dom:
        content = desktop.scrape(url)
        return f"URL:{url}\nContent:\n{content}"

    desktop_state = desktop.get_state(use_vision=False, use_dom=use_dom)
    tree_state = desktop_state.tree_state
    if not tree_state.dom_node:
        return f"No DOM information found. Please open {url} in browser first."
    dom_node = tree_state.dom_node
    vertical_scroll_percent = dom_node.vertical_scroll_percent
    content = "\n".join([node.text for node in tree_state.dom_informative_nodes])
    header_status = "Reached top" if vertical_scroll_percent <= 0 else "Scroll up to see more"
    footer_status = (
        "Reached bottom" if vertical_scroll_percent >= 100 else "Scroll down to see more"
    )
    return f"URL:{url}\nContent:\n{header_status}\n{content}\n{footer_status}"


@mcp.tool(
    name="MultiSelect",
    description="Selects multiple items such as files, folders, or checkboxes if press_ctrl=True, or performs multiple clicks if False.",
    annotations=ToolAnnotations(
        title="MultiSelect",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Multi-Select-Tool")
def multi_select_tool(
    locs: list[list[int]], press_ctrl: bool | str = True, ctx: Context = None
) -> str:
    press_ctrl = press_ctrl is True or (
        isinstance(press_ctrl, str) and press_ctrl.lower() == "true"
    )
    desktop.multi_select(press_ctrl, locs)
    elements_str = "\n".join([f"({loc[0]},{loc[1]})" for loc in locs])
    return f"Multi-selected elements at:\n{elements_str}"


@mcp.tool(
    name="MultiEdit",
    description="Enters text into multiple input fields at specified coordinates [[x,y,text], ...].",
    annotations=ToolAnnotations(
        title="MultiEdit",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Multi-Edit-Tool")
def multi_edit_tool(locs: list[list], ctx: Context = None) -> str:
    desktop.multi_edit(locs)
    elements_str = ", ".join([f"({e[0]},{e[1]}) with text '{e[2]}'" for e in locs])
    return f"Multi-edited elements at: {elements_str}"


@mcp.tool(
    name="Clipboard",
    description='Manages Windows clipboard operations. Use mode="get" to read current clipboard content, mode="set" to set clipboard text.',
    annotations=ToolAnnotations(
        title="Clipboard",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Clipboard-Tool")
def clipboard_tool(
    mode: Literal["get", "set"], text: str | None = None, ctx: Context = None
) -> str:
    try:
        import win32clipboard

        if mode == "get":
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                    data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                    return f"Clipboard content:\n{data}"
                else:
                    return "Clipboard is empty or contains non-text data."
            finally:
                win32clipboard.CloseClipboard()
        elif mode == "set":
            if text is None:
                return "Error: text parameter required for set mode."
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
                return f"Clipboard set to: {text[:100]}{'...' if len(text) > 100 else ''}"
            finally:
                win32clipboard.CloseClipboard()
        else:
            return 'Error: mode must be either "get" or "set".'
    except Exception as e:
        return f"Error managing clipboard: {str(e)}"


@mcp.tool(
    name="Process",
    description='Manages system processes. Use mode="list" to list running processes with filtering and sorting options. Use mode="kill" to terminate processes by PID or name.',
    annotations=ToolAnnotations(
        title="Process",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Process-Tool")
def process_tool(
    mode: Literal["list", "kill"],
    name: str | None = None,
    pid: int | None = None,
    sort_by: Literal["memory", "cpu", "name"] = "memory",
    limit: int = 20,
    force: bool | str = False,
    ctx: Context = None,
) -> str:
    try:
        if mode == "list":
            return desktop.list_processes(name=name, sort_by=sort_by, limit=limit)
        elif mode == "kill":
            force = force is True or (isinstance(force, str) and force.lower() == "true")
            return desktop.kill_process(name=name, pid=pid, force=force)
        else:
            return 'Error: mode must be either "list" or "kill".'
    except Exception as e:
        return f"Error managing processes: {str(e)}"


@mcp.tool(
    name="SystemInfo",
    description="Returns system information including CPU usage, memory usage, disk space, network stats, and uptime. Useful for monitoring system health remotely.",
    annotations=ToolAnnotations(
        title="SystemInfo",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "SystemInfo-Tool")
def system_info_tool(ctx: Context = None) -> str:
    try:
        return desktop.get_system_info()
    except Exception as e:
        return f"Error getting system info: {str(e)}"


@mcp.tool(
    name="Notification",
    description="Sends a Windows toast notification with a title and message. Useful for alerting the user remotely.",
    annotations=ToolAnnotations(
        title="Notification",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Notification-Tool")
def notification_tool(title: str, message: str, ctx: Context = None) -> str:
    try:
        return desktop.send_notification(title, message)
    except Exception as e:
        return f"Error sending notification: {str(e)}"


@mcp.tool(
    name="LockScreen",
    description="Locks the Windows workstation. Requires the user to enter their password to unlock.",
    annotations=ToolAnnotations(
        title="LockScreen",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "LockScreen-Tool")
def lock_screen_tool(ctx: Context = None) -> str:
    try:
        return desktop.lock_screen()
    except Exception as e:
        return f"Error locking screen: {str(e)}"


@click.command()
@click.option(
    "--transport",
    help="The transport layer used by the MCP server.",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
)
@click.option(
    "--host",
    help="Host to bind the SSE/Streamable HTTP server.",
    default="localhost",
    type=str,
    show_default=True,
)
@click.option(
    "--port",
    help="Port to bind the SSE/Streamable HTTP server.",
    default=8000,
    type=int,
    show_default=True,
)
def main(transport, host, port):
    match transport:
        case "stdio":
            mcp.run(transport=transport, show_banner=False)
        case "sse" | "streamable-http":
            mcp.run(transport=transport, host=host, port=port, show_banner=False)
        case _:
            raise ValueError(f"Invalid transport: {transport}")


if __name__ == "__main__":
    main()
