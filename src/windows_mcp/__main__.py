from windows_mcp.analytics import PostHogAnalytics, with_analytics
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.providers.proxy import ProxyClient
from windows_mcp.desktop.service import Desktop, Size
from windows_mcp.watchdog.service import WatchDog
from contextlib import asynccontextmanager
from fastmcp.utilities.types import Image
from PIL import Image as PILImage
from dataclasses import dataclass, field
from windows_mcp.auth import AuthClient
from mcp.types import ToolAnnotations
from fastmcp import FastMCP, Context
from windows_mcp import filesystem
from dotenv import load_dotenv
from textwrap import dedent
import windows_mcp.uia as uia
from typing import Literal
from enum import Enum
import logging
import asyncio
import click
import time
import os
import io

logger = logging.getLogger(__name__)

load_dotenv()


@dataclass
class Config:
    mode: str
    sandbox_id: str = field(default="")
    api_key: str = field(default="")


MAX_IMAGE_WIDTH, MAX_IMAGE_HEIGHT = 1920, 1080

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
    watchdog.set_focus_callback(desktop.tree.on_focus_change)

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

    Raises:
        ValueError: If loc does not have exactly 2 elements.
        RuntimeError: If desktop service is not initialized in logical mode.
    """
    if len(loc) != 2:
        raise ValueError("loc must be [x, y]")
    if coordinate_system == "logical":
        if desktop is None:
            raise RuntimeError("Desktop service is not initialized.")
        scale = desktop.get_dpi_scaling()
        return [round(loc[0] * scale), round(loc[1] * scale)]
    return loc


def _region_to_physical(region: list[int], coordinate_system: str) -> list[int]:
    """Convert a region [x, y, width, height] to physical space if needed."""
    if len(region) != 4:
        raise ValueError("region must be [x, y, width, height]")
    if coordinate_system == "logical":
        if desktop is None:
            raise RuntimeError("Desktop service is not initialized.")
        scale = desktop.get_dpi_scaling()
        return [round(v * scale) for v in region]
    return region


def _path_to_physical(path: list[list[int]], coordinate_system: str) -> list[list[int]]:
    """Convert a list of [x, y] waypoints to physical space if needed."""
    for i, p in enumerate(path):
        if len(p) != 2:
            raise ValueError(f"waypoint {i} must be [x, y], got {p}")
    if coordinate_system == "logical":
        if desktop is None:
            raise RuntimeError("Desktop service is not initialized.")
        scale = desktop.get_dpi_scaling()
        return [[round(p[0] * scale), round(p[1] * scale)] for p in path]
    return path


@mcp.tool(
    name="App",
    description="Manages Windows applications with six modes: 'launch' (opens the prescribed application), 'resize' (adjusts active window size/position), 'switch' (brings specific window into focus), 'minimize'/'maximize'/'close'/'fullscreen'/'restore' (window control).",
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
    mode: Literal[
        "launch", "resize", "switch", "minimize", "maximize", "close", "fullscreen", "restore"
    ] = "launch",
    name: str | None = None,
    window_loc: list[int] | None = None,
    window_size: list[int] | None = None,
    ctx: Context = None,
):
    if mode in ("minimize", "maximize", "close", "fullscreen", "restore"):
        if not name:
            return "Error: name is required for window control actions"
        return desktop.window_control(name, mode)
    return desktop.app(mode, name, window_loc, window_size)


@mcp.tool(
    name="PowerShell",
    description="A comprehensive system tool for executing any PowerShell commands. Use it to navigate the file system, manage files and processes, and execute system-level operations. Capable of accessing web content (e.g., via Invoke-WebRequest), interacting with network resources, and performing complex administrative tasks. This tool provides full access to the underlying operating system capabilities, making it the primary interface for system automation, scripting, and deep system interaction.",
    annotations=ToolAnnotations(
        title="PowerShell",
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
    name="FileSystem",
    description="Manages file system operations with eight modes: 'read' (read text file contents with optional line offset/limit), 'write' (create or overwrite a file, set append=True to append), 'copy' (copy file or directory to destination), 'move' (move or rename file/directory), 'delete' (delete file or directory, set recursive=True for non-empty dirs), 'list' (list directory contents with optional pattern filter), 'search' (find files matching a glob pattern), 'info' (get file/directory metadata like size, dates, type). Relative paths are resolved from the user's Desktop folder. Use absolute paths to access other locations.",
    annotations=ToolAnnotations(
        title="FileSystem",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "FileSystem-Tool")
def file_system_tool(
    mode: Literal["read", "write", "copy", "move", "delete", "list", "search", "info"],
    path: str,
    destination: str | None = None,
    content: str | None = None,
    pattern: str | None = None,
    recursive: bool | str = False,
    append: bool | str = False,
    overwrite: bool | str = False,
    offset: int | None = None,
    limit: int | None = None,
    encoding: str = "utf-8",
    show_hidden: bool | str = False,
    ctx: Context = None,
) -> str:
    try:
        from platformdirs import user_desktop_dir

        default_dir = user_desktop_dir()
        if not os.path.isabs(path):
            path = os.path.join(default_dir, path)
        if destination and not os.path.isabs(destination):
            destination = os.path.join(default_dir, destination)

        recursive = recursive is True or (
            isinstance(recursive, str) and recursive.lower() == "true"
        )
        append = append is True or (isinstance(append, str) and append.lower() == "true")
        overwrite = overwrite is True or (
            isinstance(overwrite, str) and overwrite.lower() == "true"
        )
        show_hidden = show_hidden is True or (
            isinstance(show_hidden, str) and show_hidden.lower() == "true"
        )

        match mode:
            case "read":
                return filesystem.read_file(path, offset=offset, limit=limit, encoding=encoding)
            case "write":
                if content is None:
                    return "Error: content parameter is required for write mode."
                return filesystem.write_file(path, content, append=append, encoding=encoding)
            case "copy":
                if destination is None:
                    return "Error: destination parameter is required for copy mode."
                return filesystem.copy_path(path, destination, overwrite=overwrite)
            case "move":
                if destination is None:
                    return "Error: destination parameter is required for move mode."
                return filesystem.move_path(path, destination, overwrite=overwrite)
            case "delete":
                return filesystem.delete_path(path, recursive=recursive)
            case "list":
                return filesystem.list_directory(
                    path, pattern=pattern, recursive=recursive, show_hidden=show_hidden
                )
            case "search":
                if pattern is None:
                    return "Error: pattern parameter is required for search mode."
                return filesystem.search_files(path, pattern, recursive=recursive)
            case "info":
                return filesystem.get_file_info(path)
            case _:
                return f'Error: Unknown mode "{mode}". Use: read, write, copy, move, delete, list, search, info.'
    except Exception as e:
        return f"Error in File tool: {str(e)}"


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
        scale = min(scale_width, scale_height)

        desktop_state = desktop.get_state(
            use_vision=use_vision, use_dom=use_dom, as_bytes=False, scale=scale
        )

        interactive_elements = desktop_state.tree_state.interactive_elements_to_string()
        scrollable_elements = desktop_state.tree_state.scrollable_elements_to_string()
        windows = desktop_state.windows_to_string()
        active_window = desktop_state.active_window_to_string()
        active_desktop = desktop_state.active_desktop_to_string()
        all_desktops = desktop_state.desktops_to_string()

        # Convert screenshot to bytes for vision response
        screenshot_bytes = None
        if use_vision and desktop_state.screenshot is not None:
            buffered = io.BytesIO()
            desktop_state.screenshot.save(buffered, format="PNG")
            screenshot_bytes = buffered.getvalue()
            buffered.close()
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
    {scrollable_elements or "No scrollable elements found."}""")
    ] + ([Image(data=screenshot_bytes, format="png")] if use_vision and screenshot_bytes else [])


@mcp.tool(
    name="Click",
    description=(
        "Performs mouse clicks at specified coordinates [x, y] or passing a UI element's label/id. "
        "Supports button types: 'left' for selection/activation, 'right' for context menus, 'middle'. "
        "Supports clicks: 0=hover only (no click), 1=single click (select/focus), 2=double click (open/activate). "
        "Provide either loc or label."
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
    loc: list[int] | None = None,
    label: int | None = None,
    button: Literal["left", "right", "middle"] = "left",
    clicks: int = 1,
    ctx: Context = None,
) -> str:
    if loc is None and label is None:
        raise ValueError("Either loc or label must be provided.")
    if label is not None:
        if desktop.desktop_state is None:
            raise ValueError("Desktop state is empty. Please call Snapshot first.")
        try:
            loc = list(desktop.get_coordinates_from_label(label))
        except Exception as e:
            raise ValueError(f"Failed to find element with label {label}: {e}")
    if len(loc) != 2:
        raise ValueError("Location must be a list of exactly 2 integers [x, y]")
    x, y = loc[0], loc[1]
    desktop.click(loc=loc, button=button, clicks=clicks)
    num_clicks = {0: "Hover", 1: "Single", 2: "Double"}
    return f"{num_clicks.get(clicks)} {button} clicked at ({x},{y})."


@mcp.tool(
    name="Type",
    description="Types text at specified coordinates [x, y] or passing a UI element's label/id. Set clear=True to clear existing text first, False to append. Set press_enter=True to submit after typing. Set caret_position to 'start' (beginning), 'end' (end), or 'idle' (default). Provide either loc or label.",
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
    text: str,
    loc: list[int] | None = None,
    label: int | None = None,
    clear: bool | str = False,
    caret_position: Literal["start", "idle", "end"] = "idle",
    press_enter: bool | str = False,
    ctx: Context = None,
) -> str:
    if loc is None and label is None:
        raise ValueError("Either loc or label must be provided.")
    if label is not None:
        if desktop.desktop_state is None:
            raise ValueError("Desktop state is empty. Please call Snapshot first.")
        try:
            loc = list(desktop.get_coordinates_from_label(label))
        except Exception as e:
            raise ValueError(f"Failed to find element with label {label}: {e}")
    if len(loc) != 2:
        raise ValueError("Location must be a list of exactly 2 integers [x, y]")
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
    description="Scrolls at coordinates [x, y], a UI element's label/id, or current mouse position if loc=None. Type: vertical (default) or horizontal. Direction: up/down for vertical, left/right for horizontal. wheel_times controls amount (1 wheel ≈ 3-5 lines). Use for navigating long content, lists, and web pages.",
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
    loc: list[int] | None = None,
    label: int | None = None,
    type: Literal["horizontal", "vertical"] = "vertical",
    direction: Literal["up", "down", "left", "right"] = "down",
    wheel_times: int = 1,
    ctx: Context = None,
) -> str:
    if label is not None:
        if desktop.desktop_state is None:
            raise ValueError("Desktop state is empty. Please call Snapshot first.")
        try:
            loc = list(desktop.get_coordinates_from_label(label))
        except Exception as e:
            raise ValueError(f"Failed to find element with label {label}: {e}")
    if loc and len(loc) != 2:
        raise ValueError("Location must be a list of exactly 2 integers [x, y]")
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
        "Moves mouse cursor to coordinates [x, y] or passing a UI element's label/id. "
        "Set drag=True to perform a drag-and-drop operation from the current mouse position "
        "to the target coordinates. Default (drag=False) is a simple cursor move (hover). "
        "Provide either loc or label."
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
    loc: list[int] | None = None,
    label: int | None = None,
    drag: bool | str = False,
    ctx: Context = None,
) -> str:
    drag = drag is True or (isinstance(drag, str) and drag.lower() == "true")
    if loc is None and label is None:
        raise ValueError("Either loc or label must be provided.")
    if label is not None:
        if desktop.desktop_state is None:
            raise ValueError("Desktop state is empty. Please call Snapshot first.")
        try:
            loc = list(desktop.get_coordinates_from_label(label))
        except Exception as e:
            raise ValueError(f"Failed to find element with label {label}: {e}")
    if len(loc) != 2:
        raise ValueError("loc must be a list of exactly 2 integers [x, y]")
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
    time.sleep(duration)
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
    description="Selects multiple items such as files, folders, or checkboxes if press_ctrl=True, or performs multiple clicks if False. Pass locs (list of coordinates) or labels (list of UI element labels/ids).",
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
    locs: list[list[int]] | None = None,
    labels: list[int] | None = None,
    press_ctrl: bool | str = True,
    ctx: Context = None,
) -> str:
    if locs is None and labels is None:
        raise ValueError("Either locs or labels must be provided.")
    locs = locs or []
    if labels is not None:
        if desktop.desktop_state is None:
            raise ValueError("Desktop state is empty. Please call Snapshot first.")
        for label in labels:
            try:
                locs.append(list(desktop.get_coordinates_from_label(label)))
            except Exception as e:
                raise ValueError(f"Failed to find element with label {label}: {e}")

    press_ctrl = press_ctrl is True or (
        isinstance(press_ctrl, str) and press_ctrl.lower() == "true"
    )
    desktop.multi_select(press_ctrl, locs)
    elements_str = "\n".join([f"({loc[0]},{loc[1]})" for loc in locs])
    return f"Multi-selected elements at:\n{elements_str}"


@mcp.tool(
    name="MultiEdit",
    description="Enters text into multiple input fields at specified coordinates locs=[[x,y,text], ...] or using labels=[[label,text], ...]. Provide either locs or labels.",
    annotations=ToolAnnotations(
        title="MultiEdit",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Multi-Edit-Tool")
def multi_edit_tool(
    locs: list[list] | None = None, labels: list[list] | None = None, ctx: Context = None
) -> str:
    if locs is None and labels is None:
        raise ValueError("Either locs or labels must be provided.")
    locs = locs or []
    if labels is not None:
        if desktop.desktop_state is None:
            raise ValueError("Desktop state is empty. Please call Snapshot first.")
        for item in labels:
            if len(item) != 2:
                raise ValueError(f"Each label item must be [label, text]. Invalid: {item}")
            try:
                label, text = int(item[0]), item[1]
                loc = list(desktop.get_coordinates_from_label(label))
                locs.append([loc[0], loc[1], text])
            except Exception as e:
                raise ValueError(f"Failed to process label item {item}: {e}")

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
    name="Registry",
    description='Accesses the Windows Registry. Use mode="get" to read a value, mode="set" to create/update a value, mode="delete" to remove a value or key, mode="list" to list values and sub-keys under a path. Paths use PowerShell format (e.g. "HKCU:\\Software\\MyApp", "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion").',
    annotations=ToolAnnotations(
        title="Registry",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "Registry-Tool")
def registry_tool(
    mode: Literal["get", "set", "delete", "list"],
    path: str,
    name: str | None = None,
    value: str | None = None,
    type: Literal["String", "DWord", "QWord", "Binary", "MultiString", "ExpandString"] = "String",
    ctx: Context = None,
) -> str:
    try:
        if mode == "get":
            if name is None:
                return "Error: name parameter is required for get mode."
            return desktop.registry_get(path=path, name=name)
        elif mode == "set":
            if name is None:
                return "Error: name parameter is required for set mode."
            if value is None:
                return "Error: value parameter is required for set mode."
            return desktop.registry_set(path=path, name=name, value=value, reg_type=type)
        elif mode == "delete":
            return desktop.registry_delete(path=path, name=name)
        elif mode == "list":
            return desktop.registry_list(path=path)
        else:
            return 'Error: mode must be "get", "set", "delete", or "list".'
    except Exception as e:
        return f"Error accessing registry: {str(e)}"


@mcp.tool(
    name="CursorPosition",
    description="Returns the current mouse cursor position as (x, y) coordinates.",
    annotations=ToolAnnotations(
        title="CursorPosition",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "CursorPosition-Tool")
def cursor_position_tool(ctx: Context = None) -> str:
    try:
        return desktop.get_cursor_position()
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="PixelColor",
    description=(
        "Gets the RGB color value at specified screen coordinates [x, y]. "
        "Returns color as RGB values and hex code with approximate color name. "
        "Set coordinate_system='logical' to auto-convert from logical (DPI-scaled) coordinates to physical. "
        "Default is 'physical' (no conversion)."
    ),
    annotations=ToolAnnotations(
        title="PixelColor",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "PixelColor-Tool")
def pixel_color_tool(
    loc: list[int],
    coordinate_system: Literal["physical", "logical"] = "physical",
    ctx: Context = None,
) -> str:
    try:
        loc = _to_physical(loc, coordinate_system)
        return desktop.get_pixel_color(loc)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="KeyHold",
    description="Presses or releases keyboard keys independently, enabling key hold operations. Use action='down' to press and hold, 'up' to release. Supports modifier keys (shift, ctrl, alt, win) and special keys (f1-f12, enter, tab, escape, etc.). Release keys after use to avoid stuck keys.",
    annotations=ToolAnnotations(
        title="KeyHold",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "KeyHold-Tool")
def key_hold_tool(action: Literal["down", "up"], keys: list[str], ctx: Context = None) -> str:
    try:
        return desktop.key_hold(action, keys)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="ScreenInfo",
    description="Returns information about all connected monitors including resolution, position, and which is the primary display. Useful for multi-monitor setups and coordinate targeting.",
    annotations=ToolAnnotations(
        title="ScreenInfo",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "ScreenInfo-Tool")
def screen_info_tool(ctx: Context = None) -> str:
    try:
        return desktop.get_screen_info()
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="ScreenHighlight",
    description=(
        "Highlights a rectangular region on screen with a colored border for visual identification. "
        "Useful for debugging automation targets. The highlight appears briefly then disappears. "
        "Set coordinate_system='logical' to auto-convert from logical (DPI-scaled) coordinates to physical. "
        "Default is 'physical' (no conversion)."
    ),
    annotations=ToolAnnotations(
        title="ScreenHighlight",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "ScreenHighlight-Tool")
def screen_highlight_tool(
    loc: list[int],
    size: list[int],
    duration: float = 2.0,
    color: Literal["red", "green", "blue", "yellow"] = "red",
    coordinate_system: Literal["physical", "logical"] = "physical",
    ctx: Context = None,
) -> str:
    try:
        loc = _to_physical(loc, coordinate_system)
        size = _to_physical(size, coordinate_system)
        return desktop.highlight_region(loc, size, duration, color)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="MousePath",
    description=(
        "Moves the mouse cursor smoothly through a series of waypoints. "
        "Each waypoint is [x, y]. The movement is interpolated over the specified duration for smooth animation. "
        "Set coordinate_system='logical' to auto-convert from logical (DPI-scaled) coordinates to physical. "
        "Default is 'physical' (no conversion)."
    ),
    annotations=ToolAnnotations(
        title="MousePath",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "MousePath-Tool")
def mouse_path_tool(
    path: list[list[int]],
    duration: float = 0.5,
    coordinate_system: Literal["physical", "logical"] = "physical",
    ctx: Context = None,
) -> str:
    try:
        path = _path_to_physical(path, coordinate_system)
        return desktop.mouse_path(path, duration)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="ScreenReader",
    description=(
        "Reads text from a screen region using OCR (Optical Character Recognition). "
        "Uses Windows built-in OCR engine. Specify a region [x, y, width, height] to read from a specific area, "
        "or omit for the full screen. "
        "Set coordinate_system='logical' to auto-convert from logical (DPI-scaled) coordinates to physical. "
        "Default is 'physical' (no conversion)."
    ),
    annotations=ToolAnnotations(
        title="ScreenReader",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "ScreenReader-Tool")
def screen_reader_tool(
    region: list[int] | None = None,
    language: str = "en",
    coordinate_system: Literal["physical", "logical"] = "physical",
    ctx: Context = None,
) -> str:
    try:
        if region is not None:
            region = _region_to_physical(region, coordinate_system)
        return desktop.read_screen_text(region, language)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="WaitForChange",
    description=(
        "Waits until a screen region visually changes beyond a threshold. "
        "Useful for waiting for loading to complete, animations to finish, or content to update. "
        "Compares pixel data between captures. Returns when change is detected or timeout is reached. "
        "Set coordinate_system='logical' to auto-convert from logical (DPI-scaled) coordinates to physical. "
        "Default is 'physical' (no conversion)."
    ),
    annotations=ToolAnnotations(
        title="WaitForChange",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "WaitForChange-Tool")
def wait_for_change_tool(
    region: list[int],
    timeout: float = 30.0,
    threshold: float = 0.05,
    poll_interval: float = 0.5,
    coordinate_system: Literal["physical", "logical"] = "physical",
    ctx: Context = None,
) -> str:
    try:
        region = _region_to_physical(region, coordinate_system)
        return desktop.wait_for_change(region, timeout, threshold, poll_interval)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="FindImage",
    description=(
        "Finds a template image on screen using visual template matching. "
        "Returns the center coordinates and confidence score of the best match. "
        "Requires opencv-python-headless: pip install 'windows-mcp[vision]'. "
        "Optionally restrict search to a region [x, y, width, height]. "
        "Set coordinate_system='logical' to auto-convert from logical (DPI-scaled) coordinates to physical. "
        "Default is 'physical' (no conversion)."
    ),
    annotations=ToolAnnotations(
        title="FindImage",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "FindImage-Tool")
def find_image_tool(
    template_path: str,
    region: list[int] | None = None,
    threshold: float = 0.8,
    coordinate_system: Literal["physical", "logical"] = "physical",
    ctx: Context = None,
) -> str:
    try:
        if region is not None:
            region = _region_to_physical(region, coordinate_system)
        return desktop.find_image(template_path, region, threshold)
    except Exception as e:
        return f"Error: {str(e)}"


# ============== SYSTEM CONTROL TOOLS ==============


@mcp.tool(
    name="VolumeControl",
    description="Control Windows system volume: get current level, set to specific value (0-100), mute, unmute, or toggle.",
    annotations=ToolAnnotations(title="VolumeControl", readOnlyHint=False, destructiveHint=False),
)
@with_analytics(analytics, "VolumeControl-Tool")
def volume_control_tool(
    action: Literal["get", "set", "mute", "unmute", "toggle"],
    level: int | None = None,
    ctx: Context = None,
) -> str:
    try:
        return desktop.volume_control(action, level)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="BrightnessControl",
    description="Control display brightness: get current level or set to specific value (0-100). Works on laptops; may not be supported on desktop monitors.",
    annotations=ToolAnnotations(
        title="BrightnessControl", readOnlyHint=False, destructiveHint=False
    ),
)
@with_analytics(analytics, "BrightnessControl-Tool")
def brightness_control_tool(
    action: Literal["get", "set"],
    level: int | None = None,
    ctx: Context = None,
) -> str:
    try:
        return desktop.brightness_control(action, level)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="AppList",
    description="List all running GUI applications with their PID and window title, or check if a specific application is running.",
    annotations=ToolAnnotations(title="AppList", readOnlyHint=True, destructiveHint=False),
)
@with_analytics(analytics, "AppList-Tool")
def app_list_tool(
    action: Literal["list", "isRunning"] = "list",
    name: str | None = None,
    ctx: Context = None,
) -> str:
    try:
        if action == "isRunning":
            if not name:
                return "Error: name is required for isRunning action"
            return desktop.app_is_running(name)
        return desktop.app_list()
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="Dialog",
    description="Show a Windows dialog: alert (OK/Cancel), prompt (text input), choose (dropdown selection), or fileChoose (file picker). Returns the user's response.",
    annotations=ToolAnnotations(title="Dialog", readOnlyHint=True, destructiveHint=False),
)
@with_analytics(analytics, "Dialog-Tool")
def dialog_tool(
    dialog_type: Literal["alert", "prompt", "choose", "fileChoose"],
    message: str | None = None,
    title: str | None = None,
    default_answer: str | None = None,
    choices: list[str] | None = None,
    ctx: Context = None,
) -> str:
    try:
        return desktop.show_dialog(dialog_type, message, title, default_answer, choices)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="SystemInfoExtended",
    description="Get extended Windows system information: OS version, computer name, user, uptime, battery, dark mode, WiFi network.",
    annotations=ToolAnnotations(
        title="SystemInfoExtended", readOnlyHint=True, destructiveHint=False, idempotentHint=True
    ),
)
@with_analytics(analytics, "SystemInfoExtended-Tool")
def system_info_extended_tool(ctx: Context = None) -> str:
    try:
        return desktop.system_info_extended()
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="DarkMode",
    description="Control Windows dark/light mode: get current state, enable, disable, or toggle. Applies to both apps and system theme.",
    annotations=ToolAnnotations(title="DarkMode", readOnlyHint=False, destructiveHint=False),
)
@with_analytics(analytics, "DarkMode-Tool")
def dark_mode_tool(
    action: Literal["get", "enable", "disable", "toggle"],
    ctx: Context = None,
) -> str:
    try:
        return desktop.dark_mode_control(action)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="SayText",
    description="Speak text aloud using Windows text-to-speech (SAPI). Optionally specify a voice name and speaking rate (-10 to 10).",
    annotations=ToolAnnotations(title="SayText", readOnlyHint=True, destructiveHint=False),
)
@with_analytics(analytics, "SayText-Tool")
def say_text_tool(
    text: str,
    voice: str | None = None,
    rate: int | None = None,
    ctx: Context = None,
) -> str:
    try:
        return desktop.say_text(text, voice, rate)
    except Exception as e:
        return f"Error: {str(e)}"


# ============== DEV WORKFLOW TOOLS ==============


@mcp.tool(
    name="PortCheck",
    description="Check if a network port is in use and what process owns it, or list all listening ports. Useful for dev server verification.",
    annotations=ToolAnnotations(
        title="PortCheck", readOnlyHint=True, destructiveHint=False, idempotentHint=True
    ),
)
@with_analytics(analytics, "PortCheck-Tool")
def port_check_tool(
    action: Literal["check", "list"],
    port: int | None = None,
    protocol: Literal["tcp", "udp", "both"] = "tcp",
    ctx: Context = None,
) -> str:
    try:
        return desktop.port_check(action, port, protocol)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="FileWatcher",
    description="Watch a file or directory for changes (create, modify, delete). Blocks until a change is detected or timeout expires.",
    annotations=ToolAnnotations(title="FileWatcher", readOnlyHint=True, destructiveHint=False),
)
@with_analytics(analytics, "FileWatcher-Tool")
def file_watcher_tool(
    path: str,
    timeout_seconds: int = 30,
    event: Literal["any", "create", "modify", "delete"] = "any",
    ctx: Context = None,
) -> str:
    try:
        return desktop.file_watcher(path, min(timeout_seconds, 300), event)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="SearchFiles",
    description="Search for files by name or content using PowerShell. Optionally limit search to a specific directory.",
    annotations=ToolAnnotations(
        title="SearchFiles", readOnlyHint=True, destructiveHint=False, idempotentHint=True
    ),
)
@with_analytics(analytics, "SearchFiles-Tool")
def search_files_tool(
    query: str,
    search_type: Literal["name", "content"] = "name",
    directory: str | None = None,
    max_results: int = 20,
    ctx: Context = None,
) -> str:
    try:
        return desktop.search_files(query, search_type, directory, min(max_results, 100))
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="NetworkDiagnostics",
    description="Network diagnostics: ping a host, DNS lookup, HTTP endpoint check, or list network interfaces.",
    annotations=ToolAnnotations(
        title="NetworkDiagnostics", readOnlyHint=True, destructiveHint=False, idempotentHint=True
    ),
)
@with_analytics(analytics, "NetworkDiagnostics-Tool")
def network_diagnostics_tool(
    action: Literal["ping", "dns", "http", "interfaces"],
    host: str | None = None,
    count: int = 3,
    timeout: int = 5,
    ctx: Context = None,
) -> str:
    try:
        return desktop.network_diagnostics(action, host, min(count, 10), min(timeout, 30))
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="AccessibilityInspector",
    description="Read the UI element tree of a Windows application window. Returns element hierarchy with control types, names, values, and enabled states.",
    annotations=ToolAnnotations(
        title="AccessibilityInspector", readOnlyHint=True, destructiveHint=False
    ),
)
@with_analytics(analytics, "AccessibilityInspector-Tool")
def accessibility_inspector_tool(
    app_name: str,
    max_depth: int = 3,
    ctx: Context = None,
) -> str:
    try:
        return desktop.accessibility_inspector(app_name, min(max_depth, 5))
    except Exception as e:
        return f"Error: {str(e)}"


# ============== UI ELEMENT TOOLS ==============


@mcp.tool(
    name="UIElement",
    description=(
        "Interact with UI elements in Windows applications using UIAutomation. "
        "Modes: 'get' (element tree with depth/role filter), 'find' (search by name/role), "
        "'click' (click by path or search), 'setValue' (set text/checkbox/slider value), "
        "'typeInto' (focus element + type text), 'listWindows' (all windows with details), "
        "'overview' (element role counts for app). "
        "Path format: 'role index > role index' (e.g., 'pane 1 > button 2')."
    ),
    annotations=ToolAnnotations(
        title="UIElement",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "UIElement-Tool")
def ui_element_tool(
    mode: Literal["get", "find", "click", "setValue", "typeInto", "listWindows", "overview"],
    app: str | None = None,
    path: str | None = None,
    search: str | None = None,
    role: str | None = None,
    value: str | None = None,
    text: str | None = None,
    depth: int = 2,
    clear: bool = False,
    ctx: Context = None,
) -> str:
    try:
        if mode == "get":
            if not app:
                return "Error: app is required for 'get' mode"
            return desktop.ui_element_get(app, min(depth, 5), role)
        elif mode == "find":
            if not app:
                return "Error: app is required for 'find' mode"
            if not search:
                return "Error: search is required for 'find' mode"
            return desktop.ui_element_find(app, search, role)
        elif mode == "click":
            if not app:
                return "Error: app is required for 'click' mode"
            if not path and not search:
                return "Error: path or search is required for 'click' mode"
            return desktop.ui_element_click(app, path, search)
        elif mode == "setValue":
            if not app:
                return "Error: app is required for 'setValue' mode"
            if value is None:
                return "Error: value is required for 'setValue' mode"
            if not path and not search:
                return "Error: path or search is required for 'setValue' mode"
            return desktop.ui_element_set_value(app, value, path, search)
        elif mode == "typeInto":
            if not app:
                return "Error: app is required for 'typeInto' mode"
            if text is None:
                return "Error: text is required for 'typeInto' mode"
            if not path and not search:
                return "Error: path or search is required for 'typeInto' mode"
            return desktop.ui_element_type_into(app, text, path, search, clear)
        elif mode == "listWindows":
            return desktop.ui_element_list_windows()
        elif mode == "overview":
            if not app:
                return "Error: app is required for 'overview' mode"
            return desktop.ui_element_overview(app)
        else:
            return f"Error: Unknown mode: {mode}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="WindowScreenshot",
    description=(
        "Capture a screenshot of a specific window by app name or window handle. "
        "Returns the window screenshot as an image."
    ),
    annotations=ToolAnnotations(
        title="WindowScreenshot",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "WindowScreenshot-Tool")
def window_screenshot_tool(
    app: str | None = None,
    handle: int | None = None,
    ctx: Context = None,
) -> list | str:
    try:
        if not app and not handle:
            return "Error: app or handle is required"
        img = desktop.capture_window_screenshot(app, handle)
        if img is None:
            return "Error: Could not capture window screenshot."
        # Resize if larger than max
        if img.width > MAX_IMAGE_WIDTH or img.height > MAX_IMAGE_HEIGHT:
            ratio = min(MAX_IMAGE_WIDTH / img.width, MAX_IMAGE_HEIGHT / img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), PILImage.LANCZOS)
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_bytes = buffered.getvalue()
        buffered.close()
        return [Image(data=img_bytes, format="png")]
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="MultiMonitor",
    description="Get information about all connected monitors including resolution, position, working area, and which is primary.",
    annotations=ToolAnnotations(
        title="MultiMonitor",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "MultiMonitor-Tool")
def multi_monitor_tool(ctx: Context = None) -> str:
    try:
        return desktop.get_multi_monitor_info()
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="ScreenRecord",
    description="Control screen recording using ffmpeg: start recording, stop recording, or check status. Requires ffmpeg in PATH.",
    annotations=ToolAnnotations(
        title="ScreenRecord",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "ScreenRecord-Tool")
def screen_record_tool(
    action: Literal["start", "stop", "status"] = "start",
    output_path: str | None = None,
    duration: int | None = None,
    fps: int = 15,
    ctx: Context = None,
) -> str:
    try:
        return desktop.screen_record(action, output_path, duration, min(fps, 60))
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="MenuClick",
    description="Navigate and click menu items by path in a Windows application. Use '>' to separate menu levels (e.g., 'File > Save As').",
    annotations=ToolAnnotations(
        title="MenuClick",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "MenuClick-Tool")
def menu_click_tool(
    app: str,
    menu_path: str,
    ctx: Context = None,
) -> str:
    try:
        return desktop.menu_click(app, menu_path)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="QuickLook",
    description="Open a file with its default Windows application. Similar to double-clicking the file in Explorer.",
    annotations=ToolAnnotations(
        title="QuickLook",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@with_analytics(analytics, "QuickLook-Tool")
def quick_look_tool(path: str, ctx: Context = None) -> str:
    try:
        return desktop.quick_look(path)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="WindowTiling",
    description=(
        "Arrange windows in tiling layouts. "
        "Modes: 'left'/'right'/'top'/'bottom' (half-screen tiling), "
        "'maximize', 'minimize', 'restore', 'cascade' (cascade all windows). "
        "Requires app name for single-window operations."
    ),
    annotations=ToolAnnotations(
        title="WindowTiling",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "WindowTiling-Tool")
def window_tiling_tool(
    mode: Literal["left", "right", "top", "bottom", "maximize", "minimize", "restore", "cascade"],
    app: str | None = None,
    ctx: Context = None,
) -> str:
    try:
        return desktop.window_tiling(mode, app)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="ClipboardInfo",
    description="Get detailed clipboard format information: available formats, text preview, image dimensions. More detailed than Clipboard tool's 'get' mode.",
    annotations=ToolAnnotations(
        title="ClipboardInfo",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
@with_analytics(analytics, "ClipboardInfo-Tool")
def clipboard_info_tool(ctx: Context = None) -> str:
    try:
        return desktop.get_clipboard_info()
    except Exception as e:
        return f"Error: {str(e)}"


class Transport(Enum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"

    def __str__(self):
        return self.value


class Mode(Enum):
    LOCAL = "local"
    REMOTE = "remote"

    def __str__(self):
        return self.value


@click.command()
@click.option(
    "--transport",
    help="The transport layer used by the MCP server.",
    type=click.Choice(
        [Transport.STDIO.value, Transport.SSE.value, Transport.STREAMABLE_HTTP.value]
    ),
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
    config = Config(
        mode=os.getenv("MODE", Mode.LOCAL.value).lower(),
        sandbox_id=os.getenv("SANDBOX_ID", ""),
        api_key=os.getenv("API_KEY", ""),
    )
    match config.mode:
        case Mode.LOCAL.value:
            match transport:
                case Transport.STDIO.value:
                    mcp.run(transport=Transport.STDIO.value, show_banner=False)
                case Transport.SSE.value | Transport.STREAMABLE_HTTP.value:
                    mcp.run(transport=transport, host=host, port=port, show_banner=False)
                case _:
                    raise ValueError(f"Invalid transport: {transport}")
        case Mode.REMOTE.value:
            if not config.sandbox_id:
                raise ValueError("SANDBOX_ID is required for MODE: remote")
            if not config.api_key:
                raise ValueError("API_KEY is required for MODE: remote")
            client = AuthClient(api_key=config.api_key, sandbox_id=config.sandbox_id)
            client.authenticate()
            backend = StreamableHttpTransport(url=client.proxy_url, headers=client.proxy_headers)
            proxy_mcp = FastMCP.as_proxy(ProxyClient(backend), name="windows-mcp")
            match transport:
                case Transport.STDIO.value:
                    proxy_mcp.run(transport=Transport.STDIO.value, show_banner=False)
                case Transport.SSE.value | Transport.STREAMABLE_HTTP.value:
                    proxy_mcp.run(transport=transport, host=host, port=port, show_banner=False)
                case _:
                    raise ValueError(f"Invalid transport: {transport}")
        case _:
            raise ValueError(f"Invalid mode: {config.mode}")


if __name__ == "__main__":
    main()
