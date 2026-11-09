"""Regression tests for #578: a call waited for programs the command left running.

``Popen.communicate()`` returns at EOF on the output pipes. A program that the
command started without waiting for it inherits the pipe handles when it is
started with handle inheritance (``Start-Process -RedirectStandardOutput``, or
any ``CreateProcess`` with bInheritHandles), so EOF came only when that program
exited. The call ran until then, or hit the timeout and reported a command
that had already finished as timed out, without its output.
"""

import io
import os
import subprocess
import sys
import time

import psutil
import pytest

from windows_mcp.powershell import PowerShellExecutor
from windows_mcp.powershell.utils import run_with_graceful_timeout

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows pipe inheritance")

# The child starts a grandchild that inherits its stdout and stderr, prints the
# grandchild's PID and exits at once. The grandchild sleeps for LINGER seconds.
LINGER = 20
CHILD = (
    "import subprocess, sys; "
    f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep({LINGER})'], close_fds=False); "
    "print(p.pid, flush=True)"
)


def _kill(pid: int) -> None:
    try:
        psutil.Process(pid).kill()
    except psutil.NoSuchProcess:
        pass


@pytest.mark.parametrize("timeout", [30, 3])
def test_returns_when_the_process_exits(timeout):
    started = time.monotonic()
    result = run_with_graceful_timeout(
        [sys.executable, "-c", CHILD], capture_output=True, text=True, timeout=timeout
    )
    elapsed = time.monotonic() - started
    grandchild = int(result.stdout.split()[0])
    try:
        assert psutil.pid_exists(grandchild), "the grandchild should still hold the pipes"
        assert result.returncode == 0
        # The grandchild runs 20 s; allow slow process startup on CI.
        assert elapsed < 8, f"waited {elapsed:.1f}s for the grandchild"
    finally:
        _kill(grandchild)


def test_powershell_start_process_with_redirect_returns_at_once(tmp_path):
    # The command from #578: Start-Process returns at once, PowerShell exits,
    # and the started program keeps the inherited pipes until it ends.
    log = tmp_path / "ping.log"
    command = (
        "Start-Process cmd.exe -ArgumentList '/c','ping -n 15 127.0.0.1' "
        f"-RedirectStandardOutput '{log}' -WindowStyle Hidden; 'started'"
    )

    started = time.monotonic()
    output, status = PowerShellExecutor.execute_command(command, timeout=10)
    elapsed = time.monotonic() - started

    assert (output.strip(), status) == ("started", 0)
    # ping runs about 14 s; allow slow PowerShell startup on CI.
    assert elapsed < 8, f"waited {elapsed:.1f}s for ping"


def test_output_written_before_exit_is_kept():
    # Output larger than the pipe buffer is read while the process runs and
    # the tail after it exits.
    script = "import sys; sys.stdout.write('x' * 300000 + 'END')"
    result = run_with_graceful_timeout(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert len(result.stdout) == 300003
    assert result.stdout.endswith("END")


def test_text_mode_translates_newlines():
    result = run_with_graceful_timeout(
        [sys.executable, "-c", "print('a'); print('b')"], capture_output=True, text=True
    )
    assert result.stdout == "a\nb\n"


def test_binary_mode_returns_bytes():
    result = run_with_graceful_timeout([sys.executable, "-c", "print('a')"], capture_output=True)
    assert result.stdout == b"a\r\n"


def test_timeout_keeps_output_printed_before_it():
    script = "import time; print('BEFORE', flush=True); time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_with_graceful_timeout(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=1,
            grace_period=0.5,
        )
    assert caught.value.stdout.strip() == "BEFORE"


def test_unbuffered_pipes_are_read():
    # bufsize=0 gives FileIO pipes, which have no read1().
    result = run_with_graceful_timeout(
        [sys.executable, "-c", "print('a')"], capture_output=True, bufsize=0
    )
    assert result.stdout == b"a\r\n"


def _wait_for(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_reader_stops_keeping_output_once_it_was_taken():
    # A program still holding the pipe may write for hours after the call has
    # returned. That output must be drained but not stored.
    from windows_mcp.powershell.utils import _PipeReader

    read_fd, write_fd = os.pipe()
    reader = _PipeReader(open(read_fd, "rb"))
    os.write(write_fd, b"before")
    _wait_for(lambda: reader._chunks)

    assert reader.take_output() == b"before"

    for _ in range(64):
        os.write(write_fd, b"x" * 65536)
    os.close(write_fd)
    reader.join(time.monotonic() + 5)
    assert reader._chunks == []


def test_text_cut_mid_character_by_a_program_still_holding_the_pipe():
    from windows_mcp.powershell.utils import _PipeReader

    read_fd, write_fd = os.pipe()
    reader = _PipeReader(io.TextIOWrapper(open(read_fd, "rb"), encoding="utf-8"))
    try:
        os.write(write_fd, "ok\r\n中".encode("utf-8")[:-1])  # the last byte is missing
        _wait_for(lambda: len(b"".join(reader._chunks)) == 6)

        assert reader.take_output() == "ok\n"
    finally:
        os.close(write_fd)


def test_text_ending_in_cr_keeps_its_line_break_while_the_pipe_is_held():
    from windows_mcp.powershell.utils import _PipeReader

    read_fd, write_fd = os.pipe()
    reader = _PipeReader(io.TextIOWrapper(open(read_fd, "rb"), encoding="utf-8"))
    try:
        os.write(write_fd, b"done\r")
        _wait_for(lambda: reader._chunks)

        assert reader.take_output() == "done\n"
    finally:
        os.close(write_fd)


def test_readers_stop_keeping_output_when_the_call_raises(monkeypatch):
    from windows_mcp.powershell import utils

    stopped = []
    real_stop = utils._PipeReader.stop_keeping

    def spy(self):
        stopped.append(self)
        real_stop(self)

    def interrupted_wait(self, timeout=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(utils._PipeReader, "stop_keeping", spy)
    monkeypatch.setattr(subprocess.Popen, "wait", interrupted_wait)
    with pytest.raises(KeyboardInterrupt):
        run_with_graceful_timeout(
            [sys.executable, "-c", "import time; time.sleep(5)"], capture_output=True
        )
    monkeypatch.undo()

    assert len(stopped) == 2
