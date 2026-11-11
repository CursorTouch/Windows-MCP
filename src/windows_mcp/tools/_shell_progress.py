"""Progress notifications for long-running PowerShell commands (#579).

MCP clients abort a call that sends nothing for too long (Claude Code gives up
after 300 s of silence, whatever ``timeout`` the call passed), so a command
that needs longer could not be run through the tool. When the request carries
a ``progressToken``, a ``notifications/progress`` message is sent every
``PROGRESS_INTERVAL_SECONDS`` while the command runs, with the elapsed time and
the last lines of output so far. Clients that reset their idle timer on
progress then keep the call alive for as long as the tool's own timeout.

A request without a ``progressToken`` runs exactly as before: no output
callback, no notifications.
"""

from __future__ import annotations

import asyncio
import codecs
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Seconds between progress notifications while a command runs.
PROGRESS_INTERVAL_SECONDS = 15.0

#: Lines of recent output included in each progress message.
PROGRESS_TAIL_LINES = 5

#: Longest output line kept in a progress message; longer lines keep their end.
PROGRESS_MAX_LINE_CHARS = 200

# Windows PowerShell 5.1 writes its error and progress streams to stderr as
# CLIXML, which only becomes readable once the whole document has arrived.
_CLIXML_MARKER = "#< CLIXML"


def progress_token(ctx: Any) -> Any | None:
    """Return the request's ``progressToken``, or ``None`` when it has none.

    Supports FastMCP 4 (``request_context.meta`` is the raw ``_meta`` dict) and
    FastMCP 3 (``request_context.meta`` is a model with ``progressToken``).
    """
    if ctx is None:
        return None
    try:
        meta = ctx.request_context.meta
    except Exception:
        return None
    if meta is None:
        return None
    if isinstance(meta, dict):
        return meta.get("progressToken")
    return getattr(meta, "progressToken", None)


class OutputTail:
    """The last lines of a command's output, fed from the pipe reader threads.

    ``feed`` matches ``run_with_graceful_timeout``'s ``on_output`` callback. A
    carriage return ends a line too, so a progress bar that redraws itself
    shows its latest state.
    """

    def __init__(self, max_lines: int = PROGRESS_TAIL_LINES) -> None:
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._partial: dict[str, str] = {}
        self._decoders: dict[str, codecs.IncrementalDecoder] = {}
        self._skipped: set[str] = set()
        self._lock = threading.Lock()

    def feed(self, stream: str, chunk: bytes) -> None:
        """Add a chunk of raw output read from *stream* ("stdout" or "stderr")."""
        with self._lock:
            if stream in self._skipped:
                return
            decoder = self._decoders.get(stream)
            if decoder is None:
                decoder = codecs.getincrementaldecoder("utf-8")("replace")
                self._decoders[stream] = decoder
            text = self._partial.get(stream, "") + decoder.decode(chunk)
            if stream == "stderr" and _CLIXML_MARKER in text:
                # Partial CLIXML is unreadable; the final result decodes it.
                self._skipped.add(stream)
                self._partial.pop(stream, None)
                return
            *complete, partial = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            for line in complete:
                self._add(line)
            # Bound the unfinished line: output without newlines can be long.
            self._partial[stream] = partial[-PROGRESS_MAX_LINE_CHARS * 4 :]

    def _add(self, line: str) -> None:
        line = line.rstrip()
        if not line:
            return
        if len(line) > PROGRESS_MAX_LINE_CHARS:
            line = "..." + line[-(PROGRESS_MAX_LINE_CHARS - 3) :]
        self._lines.append(line)

    def lines(self) -> list[str]:
        """Return the last complete lines, oldest first."""
        with self._lock:
            return list(self._lines)


def format_progress_message(elapsed: float, timeout: float, lines: list[str]) -> str:
    """Describe a running command for a progress notification.

    Args:
        elapsed: Seconds the command has been running.
        timeout: The tool's timeout in seconds.
        lines: The last lines of output, oldest first.

    Returns:
        A short message with the elapsed time and the recent output.
    """
    message = f"PowerShell command running for {elapsed:.0f}s (timeout {timeout}s)"
    if not lines:
        return message + "; no output yet."
    return message + ". Last output:\n" + "\n".join(lines)


async def run_with_progress(
    ctx: Any,
    func: Callable[..., T],
    *args: Any,
    timeout: float,
) -> T:
    """Run the blocking *func* on a worker thread, reporting progress meanwhile.

    Without a ``progressToken`` this is ``asyncio.to_thread(func, *args)``.
    With one, *func* is also given ``on_output`` (an ``OutputTail.feed``), and
    ``ctx.report_progress`` is awaited every ``PROGRESS_INTERVAL_SECONDS``
    until it returns. Notifications are sent from the event loop, so the
    session is never touched from the worker thread. A failed notification is
    logged and does not affect the command.

    Args:
        ctx: The FastMCP context of the tool call, or ``None``.
        func: The blocking function, such as ``PowerShellExecutor.execute_command``.
        *args: Positional arguments for *func*.
        timeout: The command's timeout in seconds, shown in the messages.

    Returns:
        Whatever *func* returns.
    """
    if progress_token(ctx) is None:
        return await asyncio.to_thread(func, *args)

    tail = OutputTail()
    started = time.monotonic()
    task = asyncio.ensure_future(asyncio.to_thread(func, *args, on_output=tail.feed))
    ticks = 0
    try:
        while True:
            ticks += 1
            wait = started + ticks * PROGRESS_INTERVAL_SECONDS - time.monotonic()
            done, _ = await asyncio.wait({task}, timeout=max(wait, 0))
            if done:
                return task.result()
            elapsed = time.monotonic() - started
            try:
                await ctx.report_progress(
                    progress=round(elapsed, 1),
                    message=format_progress_message(elapsed, timeout, tail.lines()),
                )
            except Exception:
                logger.debug("Could not send a progress notification", exc_info=True)
    except asyncio.CancelledError:
        # The worker thread cannot be interrupted; let it finish on its own and
        # retrieve its outcome so it is not reported as never retrieved.
        task.add_done_callback(_ignore_outcome)
        raise


def _ignore_outcome(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()
