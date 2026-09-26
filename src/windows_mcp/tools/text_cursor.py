"""
TextCursor tool — inspecting and manipulating the caret/selection
of the currently focused Windows text control through UI Automation
"""

from fastmcp import Context
from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from windows_mcp.text_cursor import CursorAction, CursorToolResult, run_tool

_description = """
Inspect or manipulate the focused Windows text control through UIA.
Modes:
- get_info
- move_relative
- move_absolute
- select_relative
- select_absolute
- select_all
- collapse_selection
Every mode accepts `delay`, expressed in seconds. The delay occurs before
the focused UIA element is located, so the caller can focus the target
control during that interval.
Absolute move/select inputs and returned offsets both use provider-defined
UIA TextUnit_Character steps from DocumentRange start. Returned offsets can
be passed directly to absolute move/select actions.
"""


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="TextCursor",
        description=_description,
        annotations=ToolAnnotations(
            title="TextCursor",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_analytics(get_analytics(), "TextCursor-Tool")
    async def text_cursor(
        action: CursorAction,
        ctx: Context = None,
    ) -> CursorToolResult:
        return await run_tool(action)
