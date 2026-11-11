"""Process(mode="list", name=...) must list the processes with that name.

The filter kept every process whose fuzzy partial_ratio against the query
exceeded 60, so "svchost.exe" also matched "chrome.exe" and
"TextInputHost.exe" on the shared ".exe". Sorted by memory and cut at limit,
those unrelated rows pushed the real matches out: on a live machine
name="svchost.exe" returned 20 rows with 1 svchost.
"""

import psutil
import pytest

from windows_mcp.process import service
from windows_mcp.process.service import list_processes
from tests.test_process_list import FakeProcess, fake_process_iter


@pytest.fixture
def processes(monkeypatch):
    procs = [
        FakeProcess(10, "chrome.exe", 1.0, rss_mb=900),
        FakeProcess(11, "TextInputHost.exe", 1.0, rss_mb=800),
        FakeProcess(12, "etcd.exe", 1.0, rss_mb=700),
        FakeProcess(20, "svchost.exe", 1.0, rss_mb=50),
        FakeProcess(21, "svchost.exe", 1.0, rss_mb=40),
        FakeProcess(30, "notepad++.exe", 1.0, rss_mb=30),
        FakeProcess(40, "powershell.exe", 1.0, rss_mb=600),
        FakeProcess(41, "explorer.exe", 1.0, rss_mb=20),
        FakeProcess(50, "wininit.exe", 1.0, rss_mb=500),
        FakeProcess(51, "git.exe", 1.0, rss_mb=10),
        FakeProcess(60, "SearchIndexer.exe", 1.0, rss_mb=400),
        FakeProcess(61, "node.exe", 1.0, rss_mb=5),
    ]
    monkeypatch.setattr(psutil, "process_iter", fake_process_iter(procs))
    monkeypatch.setattr(service, "CPU_SAMPLE_SECONDS", 0, raising=False)
    return procs


def _names(table: str) -> list[str]:
    return [line.split()[1] for line in table.splitlines()[3:]]


@pytest.mark.parametrize("query", ["svchost.exe", "svchost", "SVCHOST"])
def test_name_filter_lists_only_matching_processes(processes, query):
    assert _names(list_processes(name=query, limit=2)) == ["svchost.exe", "svchost.exe"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [("explorer", "explorer.exe"), ("git", "git.exe"), ("node", "node.exe")],
)
def test_short_names_do_not_pull_in_unrelated_processes(processes, query, expected):
    assert _names(list_processes(name=query)) == [expected]


def test_substring_still_matches(processes):
    assert _names(list_processes(name="notepad")) == ["notepad++.exe"]


def test_misspelled_name_falls_back_to_fuzzy_matching(processes):
    assert "svchost.exe" in _names(list_processes(name="svchots"))
