"""Clipboard tool — copy/paste clipboard operations."""

import time
from typing import Literal

from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from fastmcp import Context
from fastmcp.exceptions import ToolError

# Another process (clipboard managers, RDP, Office) may hold the clipboard open
# briefly; OpenClipboard then fails with ERROR_ACCESS_DENIED (5).
OPEN_ATTEMPTS = 5
OPEN_BASE_DELAY = 0.05


def _open_clipboard(win32clipboard, *, attempts: int = OPEN_ATTEMPTS) -> None:
    """Open the clipboard, retrying with exponential backoff while it is busy.

    Raises:
        ToolError: If the clipboard is still unavailable after all attempts.
    """
    error_type = getattr(win32clipboard, "error", OSError)
    for attempt in range(attempts):
        try:
            win32clipboard.OpenClipboard()
            return
        except error_type as exc:
            if attempt == attempts - 1:
                code = getattr(exc, "winerror", None) or (exc.args[0] if exc.args else None)
                raise ToolError(
                    f"Clipboard is unavailable (error {code}): another application is "
                    "holding it open. Retry later."
                ) from exc
            time.sleep(OPEN_BASE_DELAY * (2**attempt))


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="Clipboard",
        description='Copy/paste clipboard operations. Keywords: copy, paste, cut, clipboard, text transfer. Use mode="get" to read current clipboard content, mode="set" to set clipboard text. This is the real Windows clipboard; set overwrites shared contents. Avoid using it merely to pass data between tools and coordinate known interference with the user.',
        annotations=ToolAnnotations(
            title="Clipboard",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Clipboard-Tool")
    def clipboard_tool(
        mode: Literal["get", "set"],
        text: str | None = None,
        ctx: Context = None,
    ) -> str:
        try:
            import win32clipboard

            if mode == "get":
                _open_clipboard(win32clipboard)
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
                _open_clipboard(win32clipboard)
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
                    return f"Clipboard set to: {text[:100]}{'...' if len(text) > 100 else ''}"
                finally:
                    win32clipboard.CloseClipboard()
            else:
                return 'Error: mode must be either "get" or "set".'
        except Exception as e:
            raise
