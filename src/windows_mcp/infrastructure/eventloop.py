"""Event-loop hardening for a server that outlives many desktop sessions."""

import logging
import socket
from asyncio.selector_events import BaseSelectorEventLoop

logger = logging.getLogger(__name__)

__all__ = ["install_selfpipe_guard", "SELFPIPE_REBUILD_WARN_AT"]

# Past this many rebuilds the self-pipe is not merely a casualty of the odd
# session transition, so say so once. We keep rebuilding regardless: the loop
# cannot run without it. select() on Windows rejects a call with no descriptors
# registered (WinError 10022), and under the stdio transport the self-pipe is
# often the only one, so unregistering it would take the server down.
SELFPIPE_REBUILD_WARN_AT = 20


def _selfpipe_is_open(loop: BaseSelectorEventLoop) -> bool:
    """Whether the loop's self-pipe still has a live peer.

    ``_read_from_self`` drains the socket and returns silently at EOF, so the
    only way to tell a quiet pipe from a dead one is to look: a healthy
    non-blocking socket with nothing buffered raises BlockingIOError, while a
    torn-down one peeks an immediate empty read.
    """
    try:
        return loop._ssock.recv(1, socket.MSG_PEEK) != b""
    except BlockingIOError:
        return True
    except OSError:
        return False


def _rebuild_selfpipe(loop: BaseSelectorEventLoop) -> None:
    rebuilds = getattr(loop, "_selfpipe_rebuilds", 0) + 1
    loop._selfpipe_rebuilds = rebuilds

    loop._close_self_pipe()
    loop._make_self_pipe()

    if rebuilds == SELFPIPE_REBUILD_WARN_AT:
        logger.warning(
            "The asyncio self-pipe has died %d times; something on this machine "
            "keeps tearing down loopback sockets.",
            rebuilds,
        )
    else:
        logger.debug("Rebuilt a dead asyncio self-pipe (rebuild #%d)", rebuilds)


def install_selfpipe_guard() -> None:
    """Rebuild asyncio's self-pipe instead of busy-looping when Windows kills it.

    asyncio wakes its event loop through a loopback socketpair. A Windows
    session or display transition tears that idle pair down, and the loop never
    notices: ``BaseSelectorEventLoop._read_from_self`` reads the resulting EOF,
    hits its ``break``, and returns without unregistering anything. The socket
    stays permanently readable, so ``select`` returns instantly on every pass
    and the process pins a full CPU core -- silently, with no exception and no
    log line, until it is killed. Windows-MCP is long-lived on a desktop that
    gets locked and unlocked all day, so it meets this far more often than most
    asyncio programs do.

    This is upstream CPython python/cpython#156333, reported there against the
    proactor loop; the selector loop we run reaches the same dead end through
    ``_read_from_self``. Installing the guard is idempotent and a no-op on a
    healthy loop.

    Related issue: #392
    """
    original = BaseSelectorEventLoop._read_from_self
    if getattr(original, "_selfpipe_guard", False):
        return

    def _read_from_self(self: BaseSelectorEventLoop) -> None:
        try:
            original(self)
        except OSError:
            # An abortively closed peer surfaces as ConnectionResetError
            # straight out of recv(); upstream lets it escape into the loop's
            # exception handler and leaves the socket registered anyway.
            pass
        else:
            if _selfpipe_is_open(self):
                return
        _rebuild_selfpipe(self)

    _read_from_self._selfpipe_guard = True
    BaseSelectorEventLoop._read_from_self = _read_from_self
