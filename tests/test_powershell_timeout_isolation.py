from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from windows_mcp.powershell import utils


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 4242
        self.args = ["powershell.exe"]
        self.communicate_calls = 0
        self.signal_calls: list[int] = []
        self.kill_calls = 0

    def __enter__(self) -> "FakeProcess":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False

    def communicate(self, input=None, timeout=None):
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return b"", b""

    def poll(self) -> int:
        return -1

    def send_signal(self, value: int) -> None:
        self.signal_calls.append(value)

    def kill(self) -> None:
        self.kill_calls += 1


def test_timeout_kills_only_isolated_child_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeProcess()
    popen_kwargs: dict[str, object] = {}
    taskkill_calls: list[list[str]] = []

    def fake_popen(*args, **kwargs):
        popen_kwargs.update(kwargs)
        return fake

    def fake_run(command, **kwargs):
        taskkill_calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(utils.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(utils.subprocess, "run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        utils.run_with_graceful_timeout(
            ["powershell.exe", "-NoProfile"],
            capture_output=True,
            timeout=0.1,
            grace_period=0.1,
        )

    flags = int(popen_kwargs["creationflags"])
    assert flags & utils._CREATE_NEW_PROCESS_GROUP == utils._CREATE_NEW_PROCESS_GROUP
    assert flags & utils._CREATE_NO_WINDOW == utils._CREATE_NO_WINDOW
    assert fake.signal_calls == []
    assert taskkill_calls == [["taskkill", "/PID", "4242", "/T", "/F"]]


def test_powershell_tool_reserves_transport_cleanup_time() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "windows_mcp"
        / "tools"
        / "shell.py"
    ).read_text(encoding="utf-8")

    assert "_TRANSPORT_CLEANUP_RESERVE_SECONDS = 2.0" in source
    assert "float(timeout) - _TRANSPORT_CLEANUP_RESERVE_SECONDS" in source
