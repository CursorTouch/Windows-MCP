"""Regression tests for #584: a timeout ended programs the command only started.

When PowerShell did not stop within the grace period after CTRL_BREAK, the
timeout path ran ``taskkill /T``. That also ended programs the script had
started to run on their own: a GUI program or a ``Start-Process`` console
program (which gets a console of its own). Only PowerShell and the console
programs it was running, with their descendants, end now.
"""

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator

import psutil
import pytest

from windows_mcp.powershell import PowerShellExecutor
from windows_mcp.powershell import utils

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows process trees")

PYTHON = sys._base_executable
PYTHONW = os.path.join(os.path.dirname(PYTHON), "pythonw.exe")
needs_pythonw = pytest.mark.skipif(
    not os.path.exists(PYTHONW), reason="needs pythonw.exe as a GUI program"
)


def _tagged(tag: str) -> list[psutil.Process]:
    found = []
    for process in psutil.process_iter(["cmdline"]):
        try:
            if tag in " ".join(process.info["cmdline"] or []):
                found.append(process)
        except psutil.Error:
            pass
    return found


@pytest.fixture
def tag() -> Iterator[str]:
    """A marker to find the test's processes by command line, and clean them up."""
    marker = f"wmcp584-{uuid.uuid4().hex}"
    yield marker
    for process in _tagged(marker):
        try:
            process.kill()
        except psutil.Error:
            pass


def _sleeper(tag: str, name: str, ignore_break: bool = False) -> str:
    """Python code that sleeps 60 s and carries a findable marker."""
    code = "import signal, time; "
    if ignore_break:
        code += "signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
    return code + f"time.sleep(60)  # {tag}-{name}"


def _alive(tag: str, name: str) -> bool:
    return any(p.is_running() for p in _tagged(f"{tag}-{name}"))


@needs_pythonw
def test_gui_programs_are_told_apart_from_console_programs() -> None:
    gui = subprocess.Popen([PYTHONW, "-c", "import time; time.sleep(5)"])
    console = subprocess.Popen(
        [PYTHON, "-c", "import time; time.sleep(5)"], creationflags=subprocess.CREATE_NO_WINDOW
    )
    try:
        assert utils._is_gui_program(psutil.Process(gui.pid))
        assert not utils._is_gui_program(psutil.Process(console.pid))
    finally:
        gui.kill()
        console.kill()


@needs_pythonw
def test_timeout_keeps_programs_started_to_run_on_their_own(tag: str) -> None:
    # PowerShell keeps waiting for the program that ignores CTRL_BREAK, so the
    # force kill after the 2 s grace period runs.
    detached = _sleeper(tag, "start-process")
    gui = _sleeper(tag, "gui")
    waited = _sleeper(tag, "waited", ignore_break=True)
    command = (
        f"Start-Process '{PYTHON}' -ArgumentList '-c', '\"{detached}\"' -WindowStyle Hidden; "
        f"& '{PYTHONW}' -c '{gui}'; "
        f"Start-Sleep -Milliseconds 500; "
        f"& '{PYTHON}' -c '{waited}'"
    )

    started = time.monotonic()
    output, status = PowerShellExecutor.execute_command(command, timeout=3)
    elapsed = time.monotonic() - started
    time.sleep(0.5)

    assert "timed out" in output, output
    if elapsed < 3 + 2:
        pytest.skip("PowerShell ended on CTRL_BREAK, so the force kill did not run")
    assert _alive(tag, "start-process"), "Start-Process program was ended"
    assert _alive(tag, "gui"), "GUI program was ended"
    assert not _alive(tag, "waited"), "console program the command was running survived"


@needs_pythonw
def test_descendants_of_a_waited_for_program_end_with_it(tag: str) -> None:
    # A console program the command runs may start GUI helpers (a test runner
    # starting a headless browser); those are part of its work and end with it.
    # The shell stands in for PowerShell: it runs the runner in its own
    # console and waits for it, as PowerShell does for a console program.
    helper = _sleeper(tag, "helper")
    runner = (
        "import subprocess, time; "
        f"subprocess.Popen([r'{PYTHONW}', '-c', {helper!r}]); "
        f"time.sleep(60)  # {tag}-runner"
    )
    shell_code = f"import subprocess; subprocess.Popen([r'{PYTHON}', '-c', {runner!r}]).wait()"
    shell = subprocess.Popen([PYTHON, "-c", shell_code], creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 10
        while not _alive(tag, "helper"):
            assert time.monotonic() < deadline, "helper did not start"
            time.sleep(0.1)

        utils.kill_command_tree(shell.pid)
        time.sleep(0.5)

        assert not _alive(tag, "runner")
        assert not _alive(tag, "helper")
    finally:
        shell.kill()


def test_missing_process_is_ignored() -> None:
    finished = subprocess.Popen([PYTHON, "-c", "pass"], creationflags=subprocess.CREATE_NO_WINDOW)
    finished.wait()
    utils.kill_command_tree(finished.pid)
