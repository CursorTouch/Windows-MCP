"""Process(mode="list") must report measured CPU usage, even on its first call.

psutil's cpu_percent() compares against the previous call on the same
process and returns 0.0 the first time, so a single pass listed every process
at 0.0% and sort_by="cpu" ranked them arbitrarily. On Windows, PID 0 (the
System Idle Process) reports idle time summed over all cores and topped every
CPU ranking once usage was measured.
"""

from types import SimpleNamespace

import psutil
import pytest

from windows_mcp.process import service
from windows_mcp.process.service import list_processes


class FakeProcess:
    def __init__(self, pid: int, name: str, cpu: float, rss_mb: float = 10.0) -> None:
        self._attrs = {
            "pid": pid,
            "name": name,
            "memory_info": SimpleNamespace(rss=int(rss_mb * 1024 * 1024)),
        }
        self.info: dict = {}
        self._cpu = cpu
        self._sampled = False

    def cpu_percent(self, interval=None) -> float:
        # Like psutil: the first call has no earlier sample to compare against.
        if not self._sampled:
            self._sampled = True
            return 0.0
        return self._cpu


def fake_process_iter(procs):
    def process_iter(attrs=None, ad_value=None):
        # Like psutil, fill .info with the requested attributes, calling
        # cpu_percent() for "cpu_percent".
        for proc in procs:
            proc.info = {
                attr: proc.cpu_percent() if attr == "cpu_percent" else proc._attrs[attr]
                for attr in attrs
            }
            yield proc

    return process_iter


@pytest.fixture
def processes(monkeypatch):
    procs = [
        FakeProcess(0, "System Idle Process", 2961.2, rss_mb=0),
        FakeProcess(100, "quiet.exe", 0.0, rss_mb=500),
        FakeProcess(200, "busy.exe", 97.5),
        FakeProcess(300, "some.exe", 12.0),
    ]
    monkeypatch.setattr(psutil, "process_iter", fake_process_iter(procs))
    monkeypatch.setattr(service, "CPU_SAMPLE_SECONDS", 0, raising=False)
    return procs


def _rows(table: str) -> list[list[str]]:
    # Title, header and rule lines come first.
    return [line.split() for line in table.splitlines()[3:]]


def test_cpu_sort_measures_usage_on_the_first_call(processes):
    rows = _rows(list_processes(sort_by="cpu", limit=10))

    assert [row[:3] for row in rows] == [
        ["200", "busy.exe", "97.5%"],
        ["300", "some.exe", "12.0%"],
        ["100", "quiet.exe", "0.0%"],
    ]


def test_system_idle_process_is_not_listed(processes):
    for sort_by in ("cpu", "memory", "name"):
        assert "System Idle Process" not in list_processes(sort_by=sort_by, limit=10)
