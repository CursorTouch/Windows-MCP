from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def runner() -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    return load_module(root / "scripts" / "run_supervisor_task.py", "runner_test")


@pytest.fixture()
def supervisor() -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    return load_module(root / "scripts" / "windows_mcp_supervisor.py", "supervisor_async_test")


def write_spec(tmp_path: Path, command: list[str], timeout: int = 10) -> tuple[Path, Path, Path, Path]:
    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    result = tmp_path / "result.json"
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "task_id": "73a88576-083c-456c-a3f0-7958b09837e8",
                "command": command,
                "cwd": str(tmp_path),
                "timeout_seconds": timeout,
                "stdout_path": str(stdout),
                "stderr_path": str(stderr),
                "result_path": str(result),
                "env_overrides": {},
            }
        ),
        encoding="utf-8",
    )
    return spec, result, stdout, stderr


def test_runner_writes_success_result_atomically(runner: ModuleType, tmp_path: Path) -> None:
    spec, result_path, stdout_path, _ = write_spec(
        tmp_path,
        [sys.executable, "-c", "print('runner-ok')"],
    )

    completed = subprocess.run(
        [sys.executable, str(Path(runner.__file__)), "--spec", str(spec)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0
    assert result_path.is_file()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["state"] == "completed"
    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert stdout_path.read_text(encoding="utf-8").strip() == "runner-ok"
    assert result_path.with_suffix(".json.tmp").exists() is False


def test_runner_timeout_persists_failure(runner: ModuleType, tmp_path: Path) -> None:
    spec, result_path, _, _ = write_spec(
        tmp_path,
        [sys.executable, "-c", "import time; time.sleep(10)"],
        timeout=1,
    )

    completed = subprocess.run(
        [sys.executable, str(Path(runner.__file__)), "--spec", str(spec)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["state"] == "failed"
    assert result["timed_out"] is True
    assert "timeout after 1s" in result["error"]


def configure_queue(supervisor: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    queue_file = tmp_path / "queue.json"
    monkeypatch.setattr(supervisor, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(supervisor, "log", lambda *args, **kwargs: None)
    return queue_file


def test_recover_queue_preserves_live_persisted_runner(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_file = configure_queue(supervisor, tmp_path, monkeypatch)
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "96183509-68f2-47a4-98d5-c07510081e26",
                    "kind": "validate_project",
                    "state": "running",
                    "runner_pid": 4444,
                    "resumable": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "pid_alive", lambda pid: pid == 4444)

    queue = supervisor.recover_queue()

    assert len(queue) == 1
    assert queue[0]["state"] == "running"
    assert queue[0]["runner_pid"] == 4444


def test_recover_queue_preserves_finished_result_for_reconciliation(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_file = configure_queue(supervisor, tmp_path, monkeypatch)
    result_file = tmp_path / "result.json"
    result_file.write_text(json.dumps({"state": "completed", "exit_code": 0}), encoding="utf-8")
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "96a77d54-c08c-49ba-b0e1-f299e0300b4d",
                    "kind": "validate_project",
                    "state": "running",
                    "runner_pid": 9999,
                    "result_file": str(result_file),
                    "resumable": False,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "pid_alive", lambda pid: False)

    queue = supervisor.recover_queue()

    assert len(queue) == 1
    assert queue[0]["state"] == "running"


def test_recover_queue_interrupts_dead_runner_and_clones_resumable_task(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_file = configure_queue(supervisor, tmp_path, monkeypatch)
    original_id = "e8d83bc5-2c22-4e5f-908f-6461d43011ff"
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": original_id,
                    "kind": "validate_project",
                    "state": "running",
                    "runner_pid": 7777,
                    "resumable": True,
                    "result_file": str(tmp_path / "missing-result.json"),
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "pid_alive", lambda pid: False)

    queue = supervisor.recover_queue()

    assert len(queue) == 2
    assert queue[0]["state"] == "interrupted"
    assert queue[1]["state"] == "pending"
    assert queue[1]["recovery_of"] == original_id
    assert "runner_pid" not in queue[1]
    assert "result_file" not in queue[1]


def test_reconcile_command_task_marks_persisted_result_completed(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_queue(supervisor, tmp_path, monkeypatch)
    result_file = tmp_path / "result.json"
    result_file.write_text(
        json.dumps(
            {
                "state": "completed",
                "exit_code": 0,
                "duration_seconds": 2.5,
                "completed_at": "2026-07-31T09:00:00-03:00",
                "timed_out": False,
                "terminated_pids": [],
                "error": "",
            }
        ),
        encoding="utf-8",
    )
    task: dict[str, object] = {
        "id": "27f60636-43b9-468f-8dfb-30bd12ef661a",
        "kind": "validate_project",
        "state": "running",
        "result_file": str(result_file),
        "history": [],
    }

    finished = supervisor.reconcile_command_tasks([task])

    assert finished == 1
    assert task["state"] == "completed"
    assert task["exit_code"] == 0
    assert task["duration_seconds"] == 2.5


def test_process_queue_launches_without_blocking(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_queue(supervisor, tmp_path, monkeypatch)
    launched: list[str] = []
    task: dict[str, object] = {
        "id": "e785eea9-f459-40e1-8bc8-0057c2038cd1",
        "kind": "validate_project",
        "state": "pending",
    }
    monkeypatch.setattr(supervisor, "reconcile_command_tasks", lambda queue: 0)
    monkeypatch.setattr(supervisor, "atomic_json", lambda path, value: None)
    monkeypatch.setattr(
        supervisor,
        "launch_command_task",
        lambda value, queue: launched.append(str(value["id"])),
    )

    duration = supervisor.process_queue([task], {"ok": True}, [], [])

    assert launched == [task["id"]]
    assert duration < 1.0


def test_runner_read_json_retries_transient_permission_error(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "spec.json"
    source.write_text(json.dumps({"task_id": "ok"}), encoding="utf-8")
    original_read_text = runner.Path.read_text
    attempts = 0

    def flaky_read_text(path: Path, *args: object, **kwargs: object) -> str:
        nonlocal attempts
        if path == source and attempts < 2:
            attempts += 1
            raise PermissionError("transient read sharing violation")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(runner.Path, "read_text", flaky_read_text)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)

    value = runner.read_json_retry(source)

    assert value == {"task_id": "ok"}
    assert attempts == 2
