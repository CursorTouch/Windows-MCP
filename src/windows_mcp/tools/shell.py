"""PowerShell tool — shell/command execution."""

from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from windows_mcp.powershell import COMMAND_TIMED_OUT, PowerShellExecutor
from windows_mcp.tools._shell_progress import run_with_progress
from fastmcp import Context

def cap_output(text: str, max_output: int) -> str:
    """Keep the head and the tail of *text* when it exceeds *max_output* chars.

    MCP clients cap the size of a tool result and pass none of it to the model
    when the cap is exceeded, so a command that printed hundreds of
    kilobytes returned nothing usable. Keeping both ends preserves the leading
    progress and the trailing errors and summary, where the useful information
    usually lives — the tail gets the larger share.
    """
    if max_output <= 0 or len(text) <= max_output:
        return text
    if max_output < 5:
        return text[:max_output]

    def make_marker(omitted: int) -> str:
        return (
            f"\n...[Output truncated: {omitted:,} of {len(text):,} characters omitted. "
            "Re-run the command writing its output to a file (for example "
            "'... | Out-File -Encoding utf8 <path>') and read that file in parts with "
            "the FileSystem tool's offset/limit, or raise max_output (0 disables "
            "capping).]...\n"
        )

    # Using the largest possible omission count gives an upper bound on the
    # marker length; the final marker can only be shorter.
    marker = make_marker(len(text))
    if len(marker) + 2 > max_output:
        marker = "..."
    budget = max_output - len(marker)
    head = max(budget * 2 // 5, 1)
    tail = budget - head
    if marker != "...":
        marker = make_marker(len(text) - head - tail)
    return text[:head] + marker + text[-tail:]


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="PowerShell",
        description="Shell/command execution. Keywords: shell, run, execute, cmd, terminal, command line, script. A comprehensive system tool for executing any PowerShell commands. Use it to navigate the file system, manage files and processes, and execute system-level operations. Capable of accessing web content (e.g., via Invoke-WebRequest), interacting with network resources, and performing complex administrative tasks. This tool provides full access to the underlying operating system capabilities, making it the primary interface for system automation, scripting, and deep system interaction. Under relaxed control policy this tool is not desktop-gated. Prefer background execution; coordinate with the user before commands known to interfere with their desktop, reusing existing authorization. Output longer than max_output characters is cut to its head and tail with a marker reporting how much was omitted; a command ended by the timeout reports Status Code: timed out (distinct from a failure exit code) and includes whatever it printed before ending. A timeout ends the command and the console programs it was running; programs it started to run on their own (GUI programs, Start-Process without -NoNewWindow) keep running.",
        annotations=ToolAnnotations(
            title="PowerShell",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_analytics(get_analytics(), "Powershell-Tool")
    async def powershell_tool(
        command: str,
        timeout: int = 30,
        max_output: int = 20000,
        ctx: Context = None,
    ) -> str:
        # Async so progress notifications go out from the event loop while the
        # command blocks a worker thread (#579); without a progressToken this
        # is the same worker-thread call with_analytics used to make.
        response, status_code = await run_with_progress(
            ctx, PowerShellExecutor.execute_command, command, timeout, timeout=timeout
        )
        status_text = "timed out" if status_code == COMMAND_TIMED_OUT else status_code
        return f"Response: {cap_output(response, max_output)}\nStatus Code: {status_text}"
