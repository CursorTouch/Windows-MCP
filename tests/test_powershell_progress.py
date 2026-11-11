"""Regression tests for #579: progress notifications for long PowerShell commands.

The PowerShell tool sent nothing until the command ended, so MCP clients that
abort silent calls (Claude Code after 300 s) gave up on long commands. When
the request carries a ``progressToken``, the tool now sends
``notifications/progress`` every ``PROGRESS_INTERVAL_SECONDS`` with the elapsed
time and the last lines of output. Without a token nothing changes.
"""

import asyncio
import re
import sys
import threading
import time

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.powershell import PowerShellExecutor
from windows_mcp.powershell.utils import run_with_graceful_timeout
from windows_mcp.tools import _shell_progress
from windows_mcp.tools import shell as shell_module
from windows_mcp.tools._shell_progress import (
    OutputTail,
    format_progress_message,
    progress_token,
    run_with_progress,
)


@pytest.fixture
def fast_progress(monkeypatch):
    monkeypatch.setattr(_shell_progress, "PROGRESS_INTERVAL_SECONDS", 0.1)


class _SlowExecutor:
    """Stands in for PowerShellExecutor: prints two lines over ~0.5 s."""

    def __init__(self) -> None:
        self.on_output_given: list[bool] = []

    def execute_command(self, command, timeout, shell=None, *, on_output=None):
        self.on_output_given.append(on_output is not None)
        for line in (b"step one\r\n", b"step two\r\n"):
            if on_output is not None:
                on_output("stdout", line)
            time.sleep(0.25)
        return "step one\nstep two", 0


def _server(monkeypatch, executor) -> FastMCP:
    monkeypatch.setattr(shell_module, "PowerShellExecutor", executor)
    mcp = FastMCP(name="progress-test")
    shell_module.register(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    return mcp


# ---------------------------------------------------------------------------
# End to end through FastMCP
# ---------------------------------------------------------------------------


async def test_progress_is_sent_while_a_slow_command_runs(monkeypatch, fast_progress):
    executor = _SlowExecutor()
    received: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress, total, message):
        received.append((progress, total, message))

    async with Client(_server(monkeypatch, executor)) as client:
        result = await client.call_tool(
            "PowerShell", {"command": "slow", "timeout": 60}, progress_handler=on_progress
        )

    assert executor.on_output_given == [True]
    assert len(received) >= 3
    progresses = [progress for progress, _, _ in received]
    assert progresses == sorted(progresses) and len(set(progresses)) == len(progresses)
    for _, _, message in received:
        assert re.match(r"PowerShell command running for \d+s \(timeout 60s\)", message)
    # The last notifications carry the output printed so far.
    assert "step one" in received[-1][2]
    # The result itself is unchanged.
    assert result.content[0].text == "Response: step one\nstep two\nStatus Code: 0"


async def test_no_progress_and_same_call_without_a_progress_token(monkeypatch, fast_progress):
    executor = _SlowExecutor()
    received: list[object] = []

    async def on_message(message):
        received.append(message)

    async with Client(_server(monkeypatch, executor), message_handler=on_message) as client:
        # Client.call_tool always registers a progress handler (and so a token);
        # the session call without a callback sends none, as Claude Code's does.
        result = await client.session.call_tool("PowerShell", {"command": "slow"})

    # No callback reaches the executor: the call is the one made before #579.
    assert executor.on_output_given == [False]
    assert not [m for m in received if "progress" in type(m).__name__.lower()]
    assert result.content[0].text == "Response: step one\nstep two\nStatus Code: 0"


# ---------------------------------------------------------------------------
# run_with_progress
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self, meta, fail: bool = False) -> None:
        self.request_context = type("RC", (), {"meta": meta})()
        self.reports: list[dict] = []
        self.fail = fail

    async def report_progress(self, progress, total=None, message=None):
        self.reports.append({"progress": progress, "total": total, "message": message})
        if self.fail:
            raise RuntimeError("client went away")


def _blocking(seconds: float, lines=()):
    def run(*args, on_output=None):
        for line in lines:
            if on_output is not None:
                on_output("stdout", line)
        time.sleep(seconds)
        return ("result", args, on_output is not None)

    return run


async def test_run_with_progress_reports_elapsed_time_and_tail(fast_progress):
    ctx = _FakeContext({"progressToken": "tok"})
    result = await run_with_progress(
        ctx, _blocking(0.45, [b"alpha\n", b"beta\n"]), "cmd", 300, timeout=300
    )

    assert result == ("result", ("cmd", 300), True)
    assert len(ctx.reports) >= 3
    first = ctx.reports[0]
    assert 0.05 <= first["progress"] <= 0.4
    assert first["message"].startswith("PowerShell command running for 0s (timeout 300s)")
    assert first["message"].endswith("Last output:\nalpha\nbeta")


async def test_run_with_progress_without_token_sends_nothing(fast_progress):
    for ctx in (None, _FakeContext({}), _FakeContext(None)):
        result = await run_with_progress(ctx, _blocking(0.3), "cmd", 5, timeout=5)
        assert result == ("result", ("cmd", 5), False)
        if ctx is not None:
            assert ctx.reports == []


async def test_failed_notifications_do_not_affect_the_command(fast_progress):
    ctx = _FakeContext({"progressToken": 7}, fail=True)
    result = await run_with_progress(ctx, _blocking(0.35), "cmd", 5, timeout=5)
    assert result == ("result", ("cmd", 5), True)
    assert len(ctx.reports) >= 2


async def test_cancelled_call_lets_the_worker_finish(fast_progress):
    finished = threading.Event()

    def run(*args, on_output=None):
        time.sleep(0.3)
        finished.set()
        return "late"

    task = asyncio.ensure_future(
        run_with_progress(_FakeContext({"progressToken": 1}), run, timeout=5)
    )
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.to_thread(finished.wait, 5)
    assert finished.is_set()


def test_progress_token_reads_fastmcp_3_and_4_contexts():
    assert progress_token(_FakeContext({"progressToken": "a"})) == "a"
    legacy = type("Meta", (), {"progressToken": 5})()
    assert progress_token(_FakeContext(legacy)) == 5
    assert progress_token(_FakeContext({})) is None
    assert progress_token(object()) is None
    assert progress_token(None) is None


# ---------------------------------------------------------------------------
# OutputTail and the message
# ---------------------------------------------------------------------------


def test_output_tail_keeps_the_last_complete_lines():
    tail = OutputTail(max_lines=3)
    tail.feed("stdout", b"one\r\ntwo\n\nthree\nfou")
    assert tail.lines() == ["one", "two", "three"]
    tail.feed("stdout", b"r\nfive\n")
    assert tail.lines() == ["three", "four", "five"]


def test_output_tail_treats_carriage_return_as_a_line_end():
    tail = OutputTail()
    tail.feed("stdout", b"10%\r20%\r30%\r")
    assert tail.lines() == ["10%", "20%", "30%"]


def test_output_tail_decodes_utf8_split_across_chunks():
    tail = OutputTail()
    data = "café ✓\n".encode()
    for i in range(len(data)):
        tail.feed("stdout", data[i : i + 1])
    assert tail.lines() == ["café ✓"]


def test_output_tail_shortens_long_lines_to_their_end():
    tail = OutputTail()
    tail.feed("stdout", b"x" * 1000 + b"END\n")
    (line,) = tail.lines()
    assert len(line) == _shell_progress.PROGRESS_MAX_LINE_CHARS
    assert line.startswith("...") and line.endswith("END")


def test_output_tail_skips_clixml_stderr_but_keeps_plain_stderr():
    tail = OutputTail()
    tail.feed("stderr", b"plain warning\n")
    tail.feed("stderr", b'#< CLIXML\r\n<Objs Version="1.1.0.1"><S S="Error">boom')
    tail.feed("stderr", b"</S></Objs>\n")
    tail.feed("stdout", b"out\n")
    assert tail.lines() == ["plain warning", "out"]


def test_format_progress_message():
    assert (
        format_progress_message(45.4, 300, [])
        == "PowerShell command running for 45s (timeout 300s); no output yet."
    )
    assert format_progress_message(90, 600, ["a", "b"]) == (
        "PowerShell command running for 90s (timeout 600s). Last output:\na\nb"
    )


# ---------------------------------------------------------------------------
# The output callback on real processes
# ---------------------------------------------------------------------------


def test_run_with_graceful_timeout_reports_output_as_it_arrives():
    seen: list[tuple[str, bytes, float]] = []
    script = (
        "import sys, time\n"
        "print('first', flush=True)\n"
        "time.sleep(0.5)\n"
        "print('second', flush=True)\n"
        "print('err', file=sys.stderr, flush=True)\n"
    )
    result = run_with_graceful_timeout(
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=10,
        on_output=lambda stream, chunk: seen.append((stream, chunk, time.monotonic())),
    )

    assert result.stdout.replace(b"\r\n", b"\n") == b"first\nsecond\n"
    assert result.stderr.replace(b"\r\n", b"\n") == b"err\n"
    stdout = b"".join(chunk for stream, chunk, _ in seen if stream == "stdout")
    ended = time.monotonic()
    assert stdout == result.stdout
    assert b"".join(c for s, c, _ in seen if s == "stderr") == result.stderr
    # "first" was reported while the process still slept, not after it ended.
    first_at = next(t for s, c, t in seen if s == "stdout" and b"first" in c)
    assert first_at < ended - 0.3


def test_a_failing_output_callback_does_not_change_the_result():
    def broken(stream, chunk):
        raise RuntimeError("callback bug")

    result = run_with_graceful_timeout(
        [sys.executable, "-c", "print('a'); print('b')"],
        capture_output=True,
        timeout=10,
        on_output=broken,
    )
    assert result.stdout.replace(b"\r\n", b"\n") == b"a\nb\n"


@pytest.mark.skipif(sys.platform != "win32", reason="runs PowerShell")
async def test_real_powershell_command_reports_its_output(monkeypatch):
    monkeypatch.setattr(_shell_progress, "PROGRESS_INTERVAL_SECONDS", 0.5)
    mcp = FastMCP(name="progress-real")
    shell_module.register(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    messages: list[str] = []

    async def on_progress(progress, total, message):
        messages.append(message)

    command = "Write-Output 'started'; Start-Sleep -Seconds 3; Write-Output 'done'"
    async with Client(mcp) as client:
        result = await client.call_tool(
            "PowerShell", {"command": command, "timeout": 30}, progress_handler=on_progress
        )

    expected, status = PowerShellExecutor.execute_command(command, 30)
    assert result.content[0].text == f"Response: {expected}\nStatus Code: {status}"
    assert messages, "expected progress while the command slept"
    assert any(m.endswith("Last output:\nstarted") for m in messages)
