from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(r"D:\Projetos\WINDOWS-MCP-TEST")
SCRIPT = ROOT / "scripts" / "test_supervisor_recovery.py"
MIGRATION_SCRIPT = ROOT / "scripts" / "migrate_supervisor_v4.ps1"


def load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("test_supervisor_recovery_probe_module", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProcess:
    def __init__(self, pid: int, ppid: int, name: str) -> None:
        self.info = {
            "pid": pid,
            "ppid": ppid,
            "name": name,
            "cmdline": [name, str(pid)],
        }


def test_process_tree_stops_on_parent_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = load_probe()
    processes = [
        FakeProcess(10, 11, "root.exe"),
        FakeProcess(11, 10, "child.exe"),
    ]
    monkeypatch.setattr(probe.psutil, "process_iter", lambda attrs: processes)

    rows = probe.process_tree(10, limit=16)

    assert [row["pid"] for row in rows] == [10, 11]


def test_process_tree_respects_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = load_probe()
    processes = [FakeProcess(pid, pid - 1, f"p{pid}.exe") for pid in range(1, 20)]
    monkeypatch.setattr(probe.psutil, "process_iter", lambda attrs: processes)

    rows = probe.process_tree(1, limit=5)

    assert len(rows) == 5
    assert [row["pid"] for row in rows] == [1, 2, 3, 4, 5]


def test_destructive_probe_is_skipped_without_execute(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "SKIPPED_NO_EXECUTE"
    guard = tmp_path / ".orquestrador" / "evidencias" / "guards" / "destructive-task-guard.log"
    assert guard.is_file()
    assert "SKIPPED_NO_EXECUTE" in guard.read_text(encoding="utf-8")



def test_recovery_probe_supports_mcp_failure_target() -> None:
    probe = load_probe()

    args = probe.parser().parse_args(["--failure-target", "mcp"])

    assert args.failure_target == "mcp"
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'result["old_mcp_pid"]' in source
    assert 'result["orphan_pids"]' in source
    assert "orphan MCP runtime processes remained after recovery" in source

def test_migration_disables_conflicting_legacy_runtime_tasks() -> None:
    source = MIGRATION_SCRIPT.read_text(encoding="utf-8-sig")

    assert "function Disable-LegacyRuntimeTasks" in source
    assert "Windows MCP GPT Watchdog" in source
    assert "Windows MCP GPT HTTP Server" in source
    assert "Windows MCP GPT HTTP Migration Once" in source
    assert "Export-ScheduledTask" in source
    assert "Disable-ScheduledTask" in source
    assert "legacy conflicting task remained enabled" in source
    assert source.index("Disable-LegacyRuntimeTasks") < source.index("Set-Stage 'preflight'")

def test_runtime_orphan_filter_ignores_auxiliary_tunnel_children() -> None:
    probe = load_probe()
    expected = (
        "D:/Projetos/WINDOWS-MCP-TEST/.venv/Scripts/python.exe "
        "-m windows_mcp serve --transport stdio"
    )
    rows = [
        {"pid": 10, "command_line": expected},
        {"pid": 11, "command_line": expected.replace("/", "\\")},
        {"pid": 12, "command_line": "cmd.exe /c codex app-server"},
        {"pid": 13, "command_line": "node codex.js app-server"},
    ]

    assert probe.runtime_process_pids(rows, expected) == [10, 11]
