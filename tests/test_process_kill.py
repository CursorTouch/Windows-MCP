"""Process(mode="kill") must find a process by its name with or without ".exe".

psutil reports Windows image names with their extension ("notepad.exe"), and
kill mode compared the given name with them exactly. So name="notepad", the
name Get-Process and Stop-Process use, terminated nothing and reported that no
such process was found, while list mode finds notepad.exe for "notepad".
"""

import psutil
import pytest

from windows_mcp.process.service import kill_process


class FakeProcess:
    def __init__(self, pid: int, name: str) -> None:
        self._attrs = {"pid": pid, "name": name}
        self.info: dict = {}
        self.calls: list[str] = []

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")


def fake_process_iter(procs):
    def process_iter(attrs=None, ad_value=None):
        # Like psutil, fill .info with the requested attributes.
        for proc in procs:
            proc.info = {attr: proc._attrs[attr] for attr in attrs}
            yield proc

    return process_iter


def no_real_process(*args, **kwargs):
    raise AssertionError("kill_process looked up a real process")


@pytest.fixture
def processes(monkeypatch):
    procs = [
        FakeProcess(4, "System"),
        FakeProcess(120, "Registry"),
        FakeProcess(4242, "notepad.exe"),
        FakeProcess(4343, "Notepad.exe"),
        FakeProcess(5000, "notepad++.exe"),
        FakeProcess(6000, "explorer.exe"),
    ]
    monkeypatch.setattr(psutil, "process_iter", fake_process_iter(procs))
    # The fake PIDs can belong to real processes: fail rather than reach one.
    monkeypatch.setattr(psutil, "Process", no_real_process)
    return procs


def _calls(procs) -> dict[int, list[str]]:
    return {proc._attrs["pid"]: proc.calls for proc in procs if proc.calls}


@pytest.mark.parametrize("name", ["notepad", "Notepad", "notepad.exe", "NOTEPAD.EXE"])
def test_name_with_or_without_exe_terminates_the_process(processes, name):
    result = kill_process(name=name)

    assert result == "Terminated: notepad.exe (PID 4242), Notepad.exe (PID 4343)"
    assert _calls(processes) == {4242: ["terminate"], 4343: ["terminate"]}


def test_force_kills_a_process_named_without_exe(processes):
    result = kill_process(name="notepad", force=True)

    assert result == "Force killed: notepad.exe (PID 4242), Notepad.exe (PID 4343)"
    assert _calls(processes) == {4242: ["kill"], 4343: ["kill"]}


def test_name_without_exe_matches_only_that_process(processes):
    result = kill_process(name="notepad++")

    assert result == "Terminated: notepad++.exe (PID 5000)"
    assert _calls(processes) == {5000: ["terminate"]}


@pytest.mark.parametrize(("name", "pid"), [("System", 4), ("registry", 120)])
def test_process_without_an_extension_still_matches(processes, name, pid):
    kill_process(name=name)

    assert _calls(processes) == {pid: ["terminate"]}


@pytest.mark.parametrize("name", ["note", "notepad.ex", "notepad.exe.exe", "calc"])
def test_partial_or_unknown_name_terminates_nothing(processes, name):
    result = kill_process(name=name)

    assert result == f'No process matching "{name}" found or access denied.'
    assert _calls(processes) == {}
