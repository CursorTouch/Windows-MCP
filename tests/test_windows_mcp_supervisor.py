from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture()
def supervisor() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "windows_mcp_supervisor.py"
    spec = importlib.util.spec_from_file_location("windows_mcp_supervisor_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def healthy_status(command: str, pid: int = 1234) -> dict[str, object]:
    return {
        "uptime_seconds": 300,
        "channels": [
            {
                "name": "main",
                "enabled": True,
                "transport_kind": "stdio",
                "probe_status": "ok",
                "details": [
                    {"key": "pid", "value": str(pid)},
                    {"key": "command", "value": command},
                ],
            }
        ],
    }


def poll_metrics(timestamp: float) -> str:
    return (
        "commands_poll_last_successful_timestamp_seconds"
        '{otel_scope_name="controlplane"} '
        f"{timestamp}\n"
    )


def test_command_normalization_and_root_detection(supervisor: ModuleType) -> None:
    command = (
        r"D:\Projetos\WINDOWS-MCP-TEST\.venv\Scripts\python.exe "
        "-m windows_mcp serve --transport stdio"
    )
    assert supervisor.normalize_command(command) == supervisor.normalize_command(
        supervisor.EXPECTED_MCP_COMMAND
    )
    assert supervisor.command_references_root(command)
    assert supervisor.command_references_root(supervisor.EXPECTED_MCP_COMMAND)


def test_profile_guard_replaces_uv_run(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile.yaml"
    backup_dir = tmp_path / "backups"
    profile.write_text(
        json.dumps(
            {
                "control_plane": {"api_key": "env:TEST_KEY"},
                "mcp": {
                    "commands": [
                        {
                            "channel": "main",
                            "command": "C:/tools/uv.exe run windows-mcp serve --transport stdio",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "PROFILE_FILE", profile)
    monkeypatch.setattr(supervisor, "PROFILE_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(supervisor, "log", lambda *args, **kwargs: None)

    result = supervisor.ensure_profile_command()

    assert result["ok"] is True
    assert result["repaired"] is True
    repaired = json.loads(profile.read_text(encoding="utf-8"))
    assert repaired["mcp"]["commands"] == [
        {"channel": "main", "command": supervisor.EXPECTED_MCP_COMMAND}
    ]
    assert repaired["mcp"]["connection_max_ttl"] == supervisor.MCP_CONNECTION_MAX_TTL
    assert result["connection_max_ttl"] == supervisor.MCP_CONNECTION_MAX_TTL
    assert list(backup_dir.iterdir())



def test_profile_guard_repairs_short_connection_ttl(
    supervisor: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "profile.yaml"
    backup_dir = tmp_path / "backups"
    profile.write_text(
        json.dumps(
            {
                "mcp": {
                    "commands": [
                        {
                            "channel": "main",
                            "command": supervisor.EXPECTED_MCP_COMMAND,
                        }
                    ],
                    "connection_max_ttl": "10m",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "PROFILE_FILE", profile)
    monkeypatch.setattr(supervisor, "PROFILE_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(supervisor, "log", lambda *args, **kwargs: None)

    result = supervisor.ensure_profile_command()

    repaired = json.loads(profile.read_text(encoding="utf-8"))
    assert result["repaired"] is True
    assert repaired["mcp"]["connection_max_ttl"] == "336h"


def test_extended_observation_is_operator_controlled(supervisor: ModuleType) -> None:
    assert supervisor.OBSERVATION_REQUIRED_HOURS == 0
    assert supervisor.DEFAULT_OBSERVATION_TARGET_SECONDS == 60
    assert supervisor.MCP_CONNECTION_MAX_TTL == "336h"
    assert supervisor.CHECKPOINT.name == "runtime-supervision.json"
    assert [path.name for path in supervisor.LEGACY_CHECKPOINTS] == [
        "loop-280h.json",
        "loop-15h.json",
    ]


def test_transport_aware_health_accepts_valid_runtime(
    supervisor: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 2_000_000_000.0
    monkeypatch.setattr(supervisor, "pid_alive", lambda pid: pid == 1234)

    result = supervisor.evaluate_runtime_health(
        healthy_status(supervisor.EXPECTED_MCP_COMMAND),
        {"main_channel_probe_status": "ok"},
        poll_metrics(now - 10),
        [],
        {1234},
        now_epoch=now,
    )

    assert result["ok"] is True
    assert result["command_ok"] is True
    assert result["process_ok"] is True
    assert result["poll_ok"] is True


@pytest.mark.parametrize(
    ("status", "system", "metrics", "events", "owned", "expected_error"),
    [
        (
            healthy_status("C:/tools/uv.exe run windows-mcp serve --transport stdio"),
            {"main_channel_probe_status": "ok"},
            poll_metrics(1_999_999_990.0),
            [],
            {1234},
            "unexpected MCP command",
        ),
        (
            healthy_status(
                "D:/Projetos/WINDOWS-MCP-TEST/.venv/Scripts/python.exe -m windows_mcp serve --transport stdio"
            ),
            {"main_channel_probe_status": "ok"},
            poll_metrics(1_999_999_700.0),
            [],
            {1234},
            "control-plane poll stale",
        ),
        (
            healthy_status(
                "D:/Projetos/WINDOWS-MCP-TEST/.venv/Scripts/python.exe -m windows_mcp serve --transport stdio"
            ),
            {"main_channel_probe_status": "ok"},
            poll_metrics(1_999_999_990.0),
            [{"seq": 9, "message": "stdio MCP command failed; requesting tunnel-client shutdown"}],
            {1234},
            "fatal MCP transport event detected",
        ),
        (
            healthy_status(
                "D:/Projetos/WINDOWS-MCP-TEST/.venv/Scripts/python.exe -m windows_mcp serve --transport stdio"
            ),
            {"main_channel_probe_status": "ok"},
            poll_metrics(1_999_999_990.0),
            [],
            set(),
            "MCP process is not a live tunnel descendant",
        ),
    ],
)
def test_transport_aware_health_rejects_failure_modes(
    supervisor: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    status: dict[str, object],
    system: dict[str, object],
    metrics: str,
    events: list[dict[str, object]],
    owned: set[int],
    expected_error: str,
) -> None:
    monkeypatch.setattr(supervisor, "pid_alive", lambda pid: pid == 1234)

    result = supervisor.evaluate_runtime_health(
        status,
        system,
        metrics,
        events,
        owned,
        now_epoch=2_000_000_000.0,
    )

    assert result["ok"] is False
    assert expected_error in result["error"]


def test_zero_poll_metric_is_allowed_during_startup(
    supervisor: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor, "pid_alive", lambda pid: pid == 1234)
    status = healthy_status(supervisor.EXPECTED_MCP_COMMAND)
    status["uptime_seconds"] = 15

    result = supervisor.evaluate_runtime_health(
        status,
        {"main_channel_probe_status": "ok"},
        poll_metrics(0.0),
        [],
        {1234},
        now_epoch=2_000_000_000.0,
    )

    assert result["ok"] is True
    assert result["poll_established"] is False
    assert result["poll_age_seconds"] is None


def test_zero_poll_metric_is_rejected_after_startup_grace(
    supervisor: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor, "pid_alive", lambda pid: pid == 1234)
    status = healthy_status(supervisor.EXPECTED_MCP_COMMAND)
    status["uptime_seconds"] = 120

    result = supervisor.evaluate_runtime_health(
        status,
        {"main_channel_probe_status": "ok"},
        poll_metrics(0.0),
        [],
        {1234},
        now_epoch=2_000_000_000.0,
    )

    assert result["ok"] is False
    assert result["poll_established"] is False
    assert "control-plane poll stale" in result["error"]


def configure_inbox(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path]:
    inbox = tmp_path / "inbox"
    rejected = tmp_path / "rejected"
    queue_file = tmp_path / "queue.json"
    monkeypatch.setattr(supervisor, "INBOX_DIR", inbox)
    monkeypatch.setattr(supervisor, "REJECTED_INBOX_DIR", rejected)
    monkeypatch.setattr(supervisor, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(supervisor, "log", lambda *args, **kwargs: None)
    return inbox, rejected, queue_file


def test_atomic_inbox_accepts_external_task(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox, _, queue_file = configure_inbox(supervisor, tmp_path, monkeypatch)
    inbox.mkdir(parents=True)
    task_id = "6c90e059-a52b-4503-b5d8-1da7ca028f65"
    source = inbox / f"{task_id}.json"
    source.write_text(
        json.dumps(
            {
                "id": task_id,
                "kind": "validate_project",
                "timeout_seconds": 1800,
            }
        ),
        encoding="utf-8",
    )
    queue: list[dict[str, object]] = []

    accepted = supervisor.drain_inbox(queue)

    assert accepted == 1
    assert queue[0]["id"] == task_id
    assert queue[0]["state"] == "pending"
    assert source.exists() is False
    persisted = json.loads(queue_file.read_text(encoding="utf-8"))
    assert persisted[0]["id"] == task_id


def test_atomic_inbox_rejects_unsupported_task(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox, rejected, _ = configure_inbox(supervisor, tmp_path, monkeypatch)
    inbox.mkdir(parents=True)
    task_id = "1ab0c6c4-d573-45e4-afeb-5df65e9113b8"
    source = inbox / f"{task_id}.json"
    source.write_text(
        json.dumps({"id": task_id, "kind": "destructive_unknown_task"}),
        encoding="utf-8",
    )
    queue: list[dict[str, object]] = []

    accepted = supervisor.drain_inbox(queue)

    assert accepted == 0
    assert queue == []
    assert source.exists() is False
    assert (rejected / source.name).exists()


def test_atomic_inbox_discards_duplicate_without_reexecuting(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox, _, queue_file = configure_inbox(supervisor, tmp_path, monkeypatch)
    inbox.mkdir(parents=True)
    task_id = "7c63b45a-360c-41fd-8144-d93640d55bbc"
    source = inbox / f"{task_id}.json"
    source.write_text(
        json.dumps({"id": task_id, "kind": "validate_project"}),
        encoding="utf-8",
    )
    queue: list[dict[str, object]] = [
        {"id": task_id, "kind": "validate_project", "state": "completed"}
    ]

    accepted = supervisor.drain_inbox(queue)

    assert accepted == 0
    assert len(queue) == 1
    assert source.exists() is False
    assert queue_file.exists() is False


def test_atomic_json_retries_transient_permission_error(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "state.json"
    real_replace = supervisor.os.replace
    attempts: list[tuple[Path, Path]] = []

    def flaky_replace(source: Path, target: Path) -> None:
        attempts.append((Path(source), Path(target)))
        if len(attempts) < 3:
            raise PermissionError("transient sharing violation")
        real_replace(source, target)

    monkeypatch.setattr(supervisor.os, "replace", flaky_replace)
    monkeypatch.setattr(supervisor.time, "sleep", lambda seconds: None)

    supervisor.atomic_json(destination, {"status": "ok"})

    assert len(attempts) == 3
    assert json.loads(destination.read_text(encoding="utf-8")) == {"status": "ok"}
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_json_uses_unique_temporary_names(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "queue.json"
    names: list[str] = []
    real_replace = supervisor.os.replace

    def capture_replace(source: Path, target: Path) -> None:
        names.append(Path(source).name)
        real_replace(source, target)

    monkeypatch.setattr(supervisor.os, "replace", capture_replace)

    supervisor.atomic_json(destination, {"value": 1})
    supervisor.atomic_json(destination, {"value": 2})

    assert len(names) == 2
    assert names[0] != names[1]
    assert all(name.startswith(".queue.json.") for name in names)
    assert json.loads(destination.read_text(encoding="utf-8")) == {"value": 2}


def test_load_json_retries_transient_permission_error(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "queue.json"
    source.write_text(json.dumps([{"id": "ok"}]), encoding="utf-8")
    original_read_text = supervisor.Path.read_text
    attempts = 0

    def flaky_read_text(path: Path, *args: object, **kwargs: object) -> str:
        nonlocal attempts
        if path == source and attempts < 2:
            attempts += 1
            raise PermissionError("transient read sharing violation")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(supervisor.Path, "read_text", flaky_read_text)
    monkeypatch.setattr(supervisor.time, "sleep", lambda seconds: None)

    value = supervisor.load_json(source, [], strict=True)

    assert value == [{"id": "ok"}]
    assert attempts == 2


def test_load_json_strict_never_replaces_corrupt_queue_with_default(
    supervisor: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "queue.json"
    source.write_text("{invalid", encoding="utf-8")

    with pytest.raises(RuntimeError, match="failed to read persisted JSON"):
        supervisor.load_json(source, [], strict=True, timeout_seconds=0)


def test_controlled_recovery_task_requires_explicit_execute(
    supervisor: ModuleType,
) -> None:
    command, _, timeout = supervisor.task_command(
        {
            "kind": "controlled_recovery",
            "timeout_seconds": 500,
            "recovery_timeout_seconds": 120,
            "stability_seconds": 45,
        }
    )

    assert command[0] == str(supervisor.PYTHON)
    assert "test_supervisor_recovery.py" in " ".join(command)
    assert "--execute" in command
    assert command[command.index("--timeout-seconds") + 1] == "120"
    assert command[command.index("--stability-seconds") + 1] == "45"
    assert command[command.index("--failure-target") + 1] == "tunnel"
    assert timeout == 500
    script = supervisor.ROOT / "scripts" / "test_supervisor_recovery.py"
    content = script.read_text(encoding="utf-8-sig")
    assert 'value.add_argument("--execute", action="store_true")' in content
    assert "SKIPPED_NO_EXECUTE" in content


def test_atomic_json_reports_persistent_lock_as_transient(
    supervisor: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "queue.json"
    moments = iter((0.0, 10.0))

    def locked_replace(source: Path, target: Path) -> None:
        raise PermissionError("persistent sharing violation")

    monkeypatch.setattr(supervisor.os, "replace", locked_replace)
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(supervisor.time, "sleep", lambda seconds: None)

    with pytest.raises(supervisor.TransientPersistenceError) as captured:
        supervisor.atomic_json(destination, [{"id": "still-in-memory"}])

    assert captured.value.path == destination
    assert "persistent sharing violation" in str(captured.value)
    assert list(tmp_path.glob(".*.tmp")) == []


def test_main_loop_handles_transient_persistence_error_source_contract(
    supervisor: ModuleType,
) -> None:
    source = (supervisor.ROOT / "scripts" / "windows_mcp_supervisor.py").read_text(
        encoding="utf-8"
    )

    assert 'except TransientPersistenceError as exc:' in source
    assert 'log("persistence_retry"' in source



def test_controlled_recovery_can_target_mcp_child(
    supervisor: ModuleType,
) -> None:
    command, _, timeout = supervisor.task_command(
        {
            "kind": "controlled_recovery",
            "timeout_seconds": 300,
            "recovery_timeout_seconds": 180,
            "stability_seconds": 60,
            "failure_target": "mcp",
        }
    )

    assert command[command.index("--failure-target") + 1] == "mcp"
    assert timeout == 300

def test_runtime_observation_tracks_pid_continuity(supervisor: ModuleType) -> None:
    task: dict[str, object] = {"kind": "runtime_observation", "state": "pending"}
    health = {"ok": True, "mcp_pid": 22}
    owned = [{"pid": 22}]
    tunnels = [{"pid": 11}]

    supervisor.sample_observation(task, health, owned, tunnels)

    assert task["initial_tunnel_pid"] == 11
    assert task["initial_mcp_pid"] == 22
    assert task["last_sample_ok"] is True
    assert task["failed_samples"] == 0
    assert task["tunnel_pid_changes"] == 0
    assert task["mcp_pid_changes"] == 0


def test_runtime_observation_rejects_recovered_pid_change(supervisor: ModuleType) -> None:
    task: dict[str, object] = {"kind": "runtime_observation", "state": "pending"}
    supervisor.sample_observation(
        task,
        {"ok": True, "mcp_pid": 22},
        [{"pid": 22}],
        [{"pid": 11}],
    )

    supervisor.sample_observation(
        task,
        {"ok": True, "mcp_pid": 44},
        [{"pid": 44}],
        [{"pid": 33}],
    )

    assert task["last_sample_ok"] is False
    assert task["failed_samples"] == 1
    assert task["tunnel_pid_changes"] == 1
    assert task["mcp_pid_changes"] == 1
    assert task["tunnel_stable"] is False
    assert task["mcp_stable"] is False
    assert task["state"] == "failed"
    assert task["completed_at"]
    assert task["failure_reason"] == "runtime PID continuity changed during observation"

def test_orphan_runtime_processes_match_abandoned_runtime_tree(
    supervisor: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = supervisor.EXPECTED_MCP_COMMAND
    cli = str(supervisor.ROOT / ".venv" / "Scripts" / "windows-mcp.exe") + " serve"
    rows = [
        {"pid": 11, "parent_pid": 999, "command": cli},
        {"pid": 12, "parent_pid": 11, "command": f"python.exe {cli}"},
        {"pid": 22, "parent_pid": 2, "command": "python tests\\worker.py"},
        {"pid": 33, "parent_pid": 888, "command": expected.replace("/", "\\")},
    ]
    monkeypatch.setattr(supervisor, "pid_alive", lambda pid: False)

    orphaned = supervisor.orphan_runtime_processes(rows)

    assert [row["pid"] for row in orphaned] == [11, 12, 33]


def test_orphan_runtime_processes_preserve_runtime_with_live_parent(
    supervisor: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = supervisor.EXPECTED_MCP_COMMAND
    rows = [{"pid": 11, "parent_pid": 777, "command": expected}]
    monkeypatch.setattr(supervisor, "pid_alive", lambda pid: pid == 777)

    assert supervisor.orphan_runtime_processes(rows) == []


def test_supervisor_main_loop_removes_orphan_runtime_before_health_check(
    supervisor: ModuleType,
) -> None:
    source = (supervisor.ROOT / "scripts" / "windows_mcp_supervisor.py").read_text(
        encoding="utf-8"
    )

    classify_index = source.index("owned, external = classify_project_processes(tunnels)")
    orphan_index = source.index("orphan_runtime = orphan_runtime_processes(external)")
    health_index = source.index("health = read_health(tunnels, owned)", orphan_index)
    assert classify_index < orphan_index < health_index
    assert '"orphan_runtime_removed"' in source



def test_runtime_observation_tolerates_single_transient_probe_timeout(
    supervisor: ModuleType,
) -> None:
    task: dict[str, object] = {"kind": "runtime_observation", "state": "pending"}
    tunnels = [{"pid": 11}]
    owned = [{"pid": 22, "parent_pid": 11, "command": supervisor.EXPECTED_MCP_COMMAND}]

    supervisor.sample_observation(task, {"ok": True, "mcp_pid": 22}, owned, tunnels)
    supervisor.sample_observation(
        task,
        {"ok": False, "error": "TimeoutError: timed out"},
        owned,
        tunnels,
    )

    assert task["state"] == "running"
    assert task["failed_samples"] == 0
    assert task["transient_unhealthy_samples"] == 1
    assert task["consecutive_unhealthy_samples"] == 1
    assert task["last_mcp_pid"] == 22
    assert task["mcp_stable"] is True
    assert task["last_sample_classification"] == "transient_unhealthy"


def test_runtime_observation_resets_transient_counter_after_recovery(
    supervisor: ModuleType,
) -> None:
    task: dict[str, object] = {"kind": "runtime_observation", "state": "pending"}
    tunnels = [{"pid": 11}]
    owned = [{"pid": 22, "parent_pid": 11, "command": supervisor.EXPECTED_MCP_COMMAND}]

    supervisor.sample_observation(task, {"ok": True, "mcp_pid": 22}, owned, tunnels)
    supervisor.sample_observation(task, {"ok": False}, owned, tunnels)
    supervisor.sample_observation(task, {"ok": True, "mcp_pid": 22}, owned, tunnels)

    assert task["state"] == "running"
    assert task["failed_samples"] == 0
    assert task["transient_unhealthy_samples"] == 1
    assert task["consecutive_unhealthy_samples"] == 0
    assert task["last_sample_ok"] is True


def test_runtime_observation_fails_after_health_threshold(
    supervisor: ModuleType,
) -> None:
    task: dict[str, object] = {"kind": "runtime_observation", "state": "pending"}
    tunnels = [{"pid": 11}]
    owned = [{"pid": 22, "parent_pid": 11, "command": supervisor.EXPECTED_MCP_COMMAND}]

    supervisor.sample_observation(task, {"ok": True, "mcp_pid": 22}, owned, tunnels)
    for _ in range(supervisor.UNHEALTHY_CYCLES_BEFORE_RESTART):
        supervisor.sample_observation(task, {"ok": False}, owned, tunnels)

    assert task["state"] == "failed"
    assert task["failed_samples"] == 1
    assert task["failure_reason"] == (
        "runtime health remained unavailable for "
        f"{supervisor.UNHEALTHY_CYCLES_BEFORE_RESTART} consecutive samples"
    )
    assert task["last_sample_classification"] == "failed_health_threshold"
