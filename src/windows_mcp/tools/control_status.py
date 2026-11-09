"""Read-only MCP control ownership status tool."""

from mcp.types import ToolAnnotations

from windows_mcp.desktop.control import get_controller


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="ControlStatus",
        description="Get desktop control owner, enabled state, gate policy, configured idle timeout and remaining user idle wait in seconds. AI control has no time limit: it lasts until the user takes over or release=true hands the desktop back, so release when the desktop work is done or before waiting on the user. Settings are read-only and changes require a server restart.",
        annotations=ToolAnnotations(
            title="ControlStatus",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def control_status_tool(release: bool = False) -> dict:
        controller = get_controller()
        return controller.release() if release else controller.status()
