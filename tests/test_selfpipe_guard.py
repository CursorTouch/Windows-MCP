"""Regression tests for issue #392 — a dead self-pipe pinning a CPU core.

asyncio wakes its event loop through a loopback socketpair. A Windows session
or display transition tears that idle pair down; the loop reads the EOF, hits
the ``break`` in ``_read_from_self``, and leaves the socket registered. It then
stays permanently readable, so ``select`` returns instantly forever and the
process burns a full core with no exception and nothing in the log.

The symptom is silent, so these tests measure it: CPU consumed over an
otherwise idle loop. Without the guard that ratio sits near 1.0.
"""

import asyncio
import logging
import socket
import struct
import threading
import time
from asyncio.selector_events import BaseSelectorEventLoop

import pytest
from click.testing import CliRunner

import windows_mcp.__main__ as cli
from windows_mcp.infrastructure.eventloop import (
    SELFPIPE_REBUILD_WARN_AT,
    install_selfpipe_guard,
)

# A spinning loop measures ~0.97; a healthy one ~0.00. Anything under this is
# unambiguously not a busy-loop, even on a contended CI runner.
BUSY_LOOP_THRESHOLD = 0.25

# Captured at import — collection happens before any test runs, so this is the
# stock method even if another module's `serve` invocation patches it later.
STOCK_READ_FROM_SELF = BaseSelectorEventLoop._read_from_self


@pytest.fixture(autouse=True)
def guard():
    """Install the guard for the test, then unpatch — it edits a stdlib class."""
    BaseSelectorEventLoop._read_from_self = STOCK_READ_FROM_SELF
    install_selfpipe_guard()
    yield
    BaseSelectorEventLoop._read_from_self = STOCK_READ_FROM_SELF


@pytest.fixture
def loop():
    loop = asyncio.SelectorEventLoop()
    yield loop
    loop.close()


def close_peer(loop):
    """Graceful teardown of the write half: the read half goes to EOF."""
    loop._csock.close()


def reset_peer(loop):
    """Abortive teardown: the read half raises ConnectionResetError instead."""
    loop._csock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))
    loop._csock.close()


def cpu_share(loop, coro_factory):
    """Run a coroutine on the loop and return CPU seconds burned per wall second."""
    cpu, wall = time.process_time(), time.perf_counter()
    loop.run_until_complete(coro_factory())
    return (time.process_time() - cpu) / (time.perf_counter() - wall)


def cpu_share_while_idle(loop, seconds=1.0, kill=None):
    async def main():
        if kill is not None:
            kill(loop)
        await asyncio.sleep(seconds)

    return cpu_share(loop, main)


class TestBusyLoopIsPrevented:
    def test_closed_selfpipe_does_not_spin(self, loop):
        assert cpu_share_while_idle(loop, kill=close_peer) < BUSY_LOOP_THRESHOLD

    def test_reset_selfpipe_does_not_spin(self, loop):
        assert cpu_share_while_idle(loop, kill=reset_peer) < BUSY_LOOP_THRESHOLD

    def test_selfpipe_is_replaced(self, loop):
        dead = loop._ssock
        cpu_share_while_idle(loop, seconds=0.2, kill=close_peer)

        assert loop._selfpipe_rebuilds == 1
        assert loop._ssock is not dead
        assert loop._ssock.fileno() in loop._selector.get_map()

    def test_wakeups_survive_the_rebuild(self, loop):
        """The point of the self-pipe: cross-thread wakeups must still land."""
        woken = asyncio.Event()

        async def main():
            close_peer(loop)
            await asyncio.sleep(0.2)  # let the guard notice and rebuild

            threading.Timer(0.1, lambda: loop.call_soon_threadsafe(woken.set)).start()
            started = time.perf_counter()
            # A loop that cannot be woken sits in select() until this timeout.
            await asyncio.wait_for(woken.wait(), timeout=5)
            return time.perf_counter() - started

        elapsed = loop.run_until_complete(main())
        assert elapsed < 2, "the loop was not woken by the rebuilt self-pipe"


class TestHealthyLoopIsUntouched:
    def test_idle_loop_stays_idle(self, loop):
        alive = loop._ssock
        assert cpu_share_while_idle(loop, seconds=0.3) < BUSY_LOOP_THRESHOLD
        assert not hasattr(loop, "_selfpipe_rebuilds")
        assert loop._ssock is alive

    def test_wakeups_still_work(self, loop):
        woken = asyncio.Event()

        async def main():
            threading.Timer(0.1, lambda: loop.call_soon_threadsafe(woken.set)).start()
            await asyncio.wait_for(woken.wait(), timeout=5)

        loop.run_until_complete(main())
        assert not hasattr(loop, "_selfpipe_rebuilds")

    def test_install_is_idempotent(self):
        install_selfpipe_guard()
        once = BaseSelectorEventLoop._read_from_self
        install_selfpipe_guard()

        assert BaseSelectorEventLoop._read_from_self is once


class TestRepeatedFailures:
    def test_every_death_is_recovered(self, loop):
        """Rebuilding is unbounded — a loop with no self-pipe cannot select()."""

        async def main():
            for _ in range(5):
                close_peer(loop)
                await asyncio.sleep(0.05)

        assert cpu_share(loop, main) < BUSY_LOOP_THRESHOLD
        assert loop._selfpipe_rebuilds == 5
        assert loop._ssock.fileno() in loop._selector.get_map()

    def test_persistent_failure_is_reported(self, loop, caplog):
        loop._selfpipe_rebuilds = SELFPIPE_REBUILD_WARN_AT - 1

        with caplog.at_level(logging.WARNING, logger="windows_mcp.infrastructure.eventloop"):
            cpu_share_while_idle(loop, seconds=0.2, kill=close_peer)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert str(SELFPIPE_REBUILD_WARN_AT) in warnings[0].getMessage()


class TestServeWiring:
    def test_serve_installs_the_guard(self, monkeypatch):
        """The fix is worthless if the CLI stops calling it."""
        monkeypatch.setattr(cli.asyncio, "set_event_loop_policy", lambda _policy: None)
        monkeypatch.setattr(cli, "discover_config_path", lambda _path: None)
        monkeypatch.setattr(cli, "_run_server", lambda **_kwargs: None)
        BaseSelectorEventLoop._read_from_self = STOCK_READ_FROM_SELF

        result = CliRunner().invoke(cli.main, ["serve"])

        assert result.exit_code == 0, result.output
        assert getattr(BaseSelectorEventLoop._read_from_self, "_selfpipe_guard", False)
