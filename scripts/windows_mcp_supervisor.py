from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(r"D:\Projetos\WINDOWS-MCP-TEST")
STATE_ROOT = ROOT / ".orquestrador" / "supervisor"
CHECKPOINT = ROOT / ".orquestrador" / "checkpoints" / "loop-15h.json"
LOCK_FILE = STATE_ROOT / "lock.json"
STATE_FILE = STATE_ROOT / "state.json"
QUEUE_FILE = STATE_ROOT / "queue.json"
INBOX_DIR = STATE_ROOT / "inbox"
REJECTED_INBOX_DIR = STATE_ROOT / "inbox-rejected"
HEARTBEAT_FILE = STATE_ROOT / "heartbeat.json"
LOG_DIR = STATE_ROOT / "logs"
LOG_FILE = LOG_DIR / "supervisor.log"
TASK_EVIDENCE_DIR = STATE_ROOT / "task-evidence"
TASK_SPEC_DIR = STATE_ROOT / "task-specs"
TASK_RESULT_DIR = STATE_ROOT / "task-results"
TASK_RUNNER = ROOT / "scripts" / "run_supervisor_task.py"
TUNNEL_BIN = ROOT / ".tunnel-client" / "bin" / "tunnel-client.exe"
PROFILE_DIR = ROOT / ".tunnel-client" / "profiles"
PROFILE_NAME = "windows-mcp-gpt-managed"
PROFILE_FILE = PROFILE_DIR / f"{PROFILE_NAME}.yaml"
EXPECTED_MCP_COMMAND = "D:/Projetos/WINDOWS-MCP-TEST/.venv/Scripts/python.exe -m windows_mcp serve --transport stdio"
HEALTH_URL_FILE = Path.home() / ".local" / "state" / "tunnel-client" / "health" / "windows-mcp-gpt.url"
TUNNEL_PID_FILE = STATE_ROOT / "tunnel-client.pid"
RUNTIME_LOG_DIR = STATE_ROOT / "runtime"
RUNTIME_LOG_FILE = RUNTIME_LOG_DIR / "tunnel-client.log"
RUNTIME_STDERR_FILE = RUNTIME_LOG_DIR / "tunnel-client.stderr.log"
PROFILE_BACKUP_DIR = STATE_ROOT / "profile-backups"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
POLL_SECONDS = 15
POLL_MAX_AGE_SECONDS = 120
UNHEALTHY_CYCLES_BEFORE_RESTART = 3
HEALTHY_CYCLES_TO_RESET_RESTARTS = 4
MAX_RESTARTS_PER_HOUR = 5
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 5
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class TransientPersistenceError(RuntimeError):
    """A Windows sharing violation that should be retried without process exit."""

    def __init__(self, path: Path, cause: BaseException) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"transient persistence failure for {path}: {cause}")


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + 5.0
        delay = 0.025
        while True:
            try:
                os.replace(temporary, path)
                return
            except PermissionError as exc:
                if time.monotonic() >= deadline:
                    raise TransientPersistenceError(path, exc) from exc
                time.sleep(delay)
                delay = min(delay * 2, 0.5)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(
    path: Path,
    default: Any,
    *,
    strict: bool = False,
    timeout_seconds: float = 5.0,
) -> Any:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    delay = 0.01
    last_error: BaseException | None = None
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return default
        except (PermissionError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                if strict:
                    raise RuntimeError(
                        f"failed to read persisted JSON after retries: {path}: {exc}"
                    ) from exc
                return default
            time.sleep(delay)
            delay = min(delay * 2, 0.25)
    if strict and last_error is not None:
        raise last_error


def rotate_logs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists() or LOG_FILE.stat().st_size < MAX_LOG_BYTES:
        return
    LOG_FILE.with_suffix(f".log.{LOG_BACKUPS}").unlink(missing_ok=True)
    for index in range(LOG_BACKUPS - 1, 0, -1):
        src = LOG_FILE.with_suffix(f".log.{index}")
        dst = LOG_FILE.with_suffix(f".log.{index + 1}")
        if src.exists():
            os.replace(src, dst)
    os.replace(LOG_FILE, LOG_FILE.with_suffix(".log.1"))


def log(event: str, **fields: Any) -> None:
    rotate_logs()
    row = {"time": now_iso(), "event": event, **fields}
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_command(command: str) -> str:
    return " ".join(str(command).replace("\\", "/").split()).casefold()


def command_references_root(command: str) -> bool:
    marker = str(ROOT).replace("\\", "/").casefold()
    normalized = str(command).replace("\\", "/").casefold()
    return marker in normalized


def ensure_profile_command() -> dict[str, Any]:
    try:
        data = json.loads(PROFILE_FILE.read_text(encoding="utf-8-sig"))
        mcp = data.setdefault("mcp", {})
        commands = mcp.get("commands")
        expected = [{"channel": "main", "command": EXPECTED_MCP_COMMAND}]
        current = commands if isinstance(commands, list) else []
        current_ok = current == expected
        forbidden_uv = any(
            "uv.exe" in normalize_command(item.get("command", ""))
            for item in current
            if isinstance(item, dict)
        )
        if current_ok and not forbidden_uv:
            return {"ok": True, "repaired": False, "command": EXPECTED_MCP_COMMAND, "error": ""}
        PROFILE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        backup = PROFILE_BACKUP_DIR / f"{PROFILE_NAME}.{stamp}.json"
        backup.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        mcp["commands"] = expected
        atomic_json(PROFILE_FILE, data)
        log(
            "profile_command_repaired",
            backup=str(backup),
            forbidden_uv=forbidden_uv,
            command=EXPECTED_MCP_COMMAND,
        )
        return {
            "ok": True,
            "repaired": True,
            "command": EXPECTED_MCP_COMMAND,
            "backup": str(backup),
            "error": "",
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        log("profile_command_invalid", error=error)
        return {"ok": False, "repaired": False, "command": "", "error": error}


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        return psutil.Process(int(pid)).is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False


def acquire_single_instance() -> None:
    existing = load_json(LOCK_FILE, {})
    existing_pid = existing.get("pid") if isinstance(existing, dict) else None
    if pid_alive(existing_pid):
        raise SystemExit(0)
    atomic_json(LOCK_FILE, {"pid": os.getpid(), "started_at": now_iso()})


def process_row(proc: psutil.Process) -> dict[str, Any]:
    try:
        command = " ".join(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        command = ""
    try:
        created = datetime.fromtimestamp(proc.create_time()).astimezone().isoformat()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        created = None
    try:
        parent_pid = proc.ppid()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        parent_pid = None
    return {
        "pid": proc.pid,
        "parent_pid": parent_pid,
        "name": proc.name() if proc.is_running() else "",
        "created_at": created,
        "command": command,
    }


def tunnel_processes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if str(proc.info.get("name") or "").casefold() != "tunnel-client.exe":
                continue
            command = " ".join(proc.info.get("cmdline") or [])
            if PROFILE_NAME.casefold() in command.casefold():
                rows.append(process_row(proc))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sorted(rows, key=lambda row: row.get("created_at") or "")


def descendant_pids(parent_pids: set[int]) -> set[int]:
    descendants: set[int] = set()
    frontier = set(parent_pids)
    while frontier:
        next_frontier: set[int] = set()
        for proc in psutil.process_iter(["pid", "ppid"]):
            try:
                pid = int(proc.info["pid"])
                ppid = int(proc.info.get("ppid") or 0)
                if ppid in frontier and pid not in descendants and pid not in parent_pids:
                    descendants.add(pid)
                    next_frontier.add(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError, ValueError):
                continue
        frontier = next_frontier
    return descendants


def classify_project_processes(tunnels: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tunnel_pids = {int(row["pid"]) for row in tunnels}
    owned_pids = descendant_pids(tunnel_pids)
    owned: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            command = " ".join(proc.info.get("cmdline") or [])
            name = str(proc.info.get("name") or "")
            if not command_references_root(command):
                continue
            if name.casefold() not in {"python.exe", "windows-mcp.exe", "uv.exe"}:
                continue
            row = process_row(proc)
            if proc.pid in owned_pids:
                owned.append(row)
            else:
                external.append(row)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return (
        sorted(owned, key=lambda row: row.get("created_at") or ""),
        sorted(external, key=lambda row: row.get("created_at") or ""),
    )


def http_body(url: str, timeout: float = 3.0, max_bytes: int = 2_000_000) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return response.read(max_bytes).decode("utf-8", errors="replace").strip()


def http_text(url: str, timeout: float = 3.0) -> str:
    return http_body(url, timeout=timeout, max_bytes=4096)


def http_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    value = json.loads(http_body(url, timeout=timeout))
    if not isinstance(value, dict):
        raise RuntimeError("JSON endpoint did not return an object")
    return value


def metric_value(metrics: str, name: str) -> float | None:
    pattern = re.compile(
        rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([-+0-9.eE]+)$",
        re.MULTILINE,
    )
    match = pattern.search(metrics)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def evaluate_runtime_health(
    status: dict[str, Any],
    system: dict[str, Any],
    metrics: str,
    events: list[dict[str, Any]],
    owned_pids: set[int],
    now_epoch: float | None = None,
) -> dict[str, Any]:
    current = time.time() if now_epoch is None else float(now_epoch)
    channels = status.get("channels") if isinstance(status.get("channels"), list) else []
    main = next(
        (
            item
            for item in channels
            if isinstance(item, dict) and item.get("name") == "main"
        ),
        {},
    )
    details = main.get("details") if isinstance(main.get("details"), list) else []
    detail_map = {
        str(item.get("key")): str(item.get("value", ""))
        for item in details
        if isinstance(item, dict)
    }
    try:
        mcp_pid = int(detail_map.get("pid", "0"))
    except ValueError:
        mcp_pid = 0
    command = detail_map.get("command", "")
    command_ok = (
        normalize_command(command) == normalize_command(EXPECTED_MCP_COMMAND)
        and "uv.exe" not in normalize_command(command)
    )
    process_ok = mcp_pid in owned_pids and pid_alive(mcp_pid)
    probe_ok = (
        main.get("enabled") is True
        and main.get("transport_kind") == "stdio"
        and main.get("probe_status") == "ok"
    )
    system_probe_ok = system.get("main_channel_probe_status") == "ok"
    poll_timestamp = metric_value(metrics, "commands_poll_last_successful_timestamp_seconds")
    uptime = float(status.get("uptime_seconds") or 0.0)
    poll_established = poll_timestamp is not None and poll_timestamp > 0.0
    poll_age = (
        max(0.0, current - poll_timestamp)
        if poll_established and poll_timestamp is not None
        else None
    )
    poll_ok = (
        poll_age is not None and poll_age <= POLL_MAX_AGE_SECONDS
    ) or (not poll_established and uptime < 90.0)
    fatal_patterns = (
        "stdio mcp command exited",
        "stdio mcp command failed",
        "stdout closed",
        "requesting tunnel-client shutdown",
        "exit status 0xc0000005",
        "panic",
        "aborted",
    )
    fatal_events: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        haystack = (
            str(event.get("message", ""))
            + " "
            + json.dumps(event.get("attrs", {}), ensure_ascii=False)
        ).casefold()
        if any(pattern in haystack for pattern in fatal_patterns):
            fatal_events.append(
                {
                    "seq": event.get("seq"),
                    "time": event.get("time"),
                    "message": event.get("message"),
                }
            )
    ok = bool(
        command_ok
        and process_ok
        and probe_ok
        and system_probe_ok
        and poll_ok
        and not fatal_events
    )
    reasons: list[str] = []
    if not command_ok:
        reasons.append("unexpected MCP command")
    if not process_ok:
        reasons.append("MCP process is not a live tunnel descendant")
    if not probe_ok:
        reasons.append("main channel probe is not ok")
    if not system_probe_ok:
        reasons.append("system main channel probe is not ok")
    if not poll_ok:
        reasons.append(f"control-plane poll stale: {poll_age}")
    if fatal_events:
        reasons.append("fatal MCP transport event detected")
    return {
        "ok": ok,
        "mcp_pid": mcp_pid,
        "mcp_command": command,
        "command_ok": command_ok,
        "process_ok": process_ok,
        "probe_ok": probe_ok,
        "system_probe_ok": system_probe_ok,
        "poll_last_success_epoch": poll_timestamp,
        "poll_established": poll_established,
        "poll_age_seconds": poll_age,
        "poll_ok": poll_ok,
        "fatal_events": fatal_events,
        "error": "; ".join(reasons),
    }


def read_health(
    tunnels: list[dict[str, Any]],
    owned: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        if len(tunnels) != 1:
            return {
                "ok": False,
                "base_url": "",
                "healthz": "",
                "readyz": "",
                "error": f"expected one tunnel, found {len(tunnels)}",
            }
        base = HEALTH_URL_FILE.read_text(encoding="utf-8").strip().rstrip("/")
        if not base.startswith("http://127.0.0.1:"):
            return {"ok": False, "error": "health URL outside loopback"}
        healthz = http_text(base + "/healthz")
        readyz = http_text(base + "/readyz")
        status = http_json(base + "/api/status")
        system = http_json(base + "/api/system")
        metrics = http_body(base + "/metrics")
        logs = http_json(base + "/api/logs?limit=300")
        events = logs.get("events") if isinstance(logs.get("events"), list) else []
        runtime = evaluate_runtime_health(
            status,
            system,
            metrics,
            events,
            {int(row["pid"]) for row in owned},
        )
        endpoint_ok = healthz == "live" and readyz == "ready"
        return {
            "ok": bool(endpoint_ok and runtime.get("ok")),
            "base_url": base,
            "healthz": healthz,
            "readyz": readyz,
            "endpoint_ok": endpoint_ok,
            "client_instance_id": status.get("client_instance_id"),
            "uptime_seconds": status.get("uptime_seconds"),
            **runtime,
        }
    except (
        OSError,
        urllib.error.URLError,
        TimeoutError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:
        return {
            "ok": False,
            "base_url": "",
            "healthz": "",
            "readyz": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def restart_history(state: dict[str, Any]) -> list[float]:
    current = time.time()
    return [float(item) for item in state.get("restart_history", []) if current - float(item) < 3600]


def start_tunnel(state: dict[str, Any], reason: str) -> bool:
    profile = ensure_profile_command()
    if not profile.get("ok"):
        state["last_error"] = f"profile invalid: {profile.get('error', '')}"
        return False
    history = restart_history(state)
    if len(history) >= MAX_RESTARTS_PER_HOUR:
        log("restart_blocked", reason=reason, count_last_hour=len(history))
        state["restart_history"] = history
        state["last_error"] = "restart limit reached"
        return False
    if tunnel_processes():
        log("restart_skipped_existing_process", reason=reason)
        return False
    backoff = min(300, 2 ** len(history))
    if backoff > 1:
        time.sleep(backoff)
    HEALTH_URL_FILE.unlink(missing_ok=True)
    TUNNEL_PID_FILE.unlink(missing_ok=True)
    RUNTIME_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stderr_handle = RUNTIME_STDERR_FILE.open(
        "a",
        encoding="utf-8",
        errors="replace",
    )
    try:
        process = subprocess.Popen(
            [
                str(TUNNEL_BIN),
                "run",
                "--profile-dir",
                str(PROFILE_DIR),
                "--profile",
                PROFILE_NAME,
                "--pid.file",
                str(TUNNEL_PID_FILE),
                "--log.file",
                str(RUNTIME_LOG_FILE),
            ],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=stderr_handle,
            stderr=stderr_handle,
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
            close_fds=True,
        )
    finally:
        stderr_handle.close()
    history.append(time.time())
    state["restart_history"] = history
    state["last_restart_reason"] = reason
    state["last_restart_at"] = now_iso()
    state["last_launched_tunnel_pid"] = process.pid
    state["last_error"] = ""
    log(
        "tunnel_started",
        reason=reason,
        backoff_seconds=backoff,
        pid=process.pid,
        command=EXPECTED_MCP_COMMAND,
    )
    return True


def stop_tunnels(tunnels: list[dict[str, Any]], reason: str) -> None:
    pids = [int(row["pid"]) for row in tunnels]
    descendants = descendant_pids(set(pids))
    for pid in pids:
        try:
            psutil.Process(pid).terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    processes = [psutil.Process(pid) for pid in pids if pid_alive(pid)]
    _, alive = psutil.wait_procs(processes, timeout=5)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    for pid in sorted(descendants, reverse=True):
        if pid_alive(pid):
            try:
                psutil.Process(pid).kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    HEALTH_URL_FILE.unlink(missing_ok=True)
    TUNNEL_PID_FILE.unlink(missing_ok=True)
    log(
        "tunnels_stopped",
        reason=reason,
        tunnel_pids=pids,
        descendant_pids=sorted(descendants),
    )


def recover_queue() -> list[dict[str, Any]]:
    queue = load_json(QUEUE_FILE, [], strict=True)
    if not isinstance(queue, list):
        queue = []
    changed = False
    for task in list(queue):
        if task.get("state") != "running":
            continue
        is_observation = task.get("kind") == "runtime_observation"
        result_file = Path(str(task.get("result_file") or ""))
        runner_pid = task.get("runner_pid")
        runner_is_active = pid_alive(runner_pid)
        result_is_ready = bool(task.get("result_file")) and result_file.is_file()
        if not is_observation and (runner_is_active or result_is_ready):
            continue
        task["state"] = "interrupted"
        task["interrupted_at"] = now_iso()
        task.setdefault("history", []).append(
            {
                "state": "interrupted",
                "at": now_iso(),
                "reason": "supervisor restart without active persisted runner",
            }
        )
        changed = True
        if task.get("resumable", False):
            clone = dict(task)
            clone["id"] = str(uuid.uuid4())
            clone["state"] = "pending"
            clone["recovery_of"] = task.get("id")
            clone["created_at"] = now_iso()
            for key in (
                "started_at",
                "completed_at",
                "runner_pid",
                "runner_started_at",
                "result_file",
                "spec_file",
                "stdout_log",
                "stderr_log",
                "exit_code",
                "error",
            ):
                clone.pop(key, None)
            clone["history"] = [
                {
                    "state": "pending",
                    "at": now_iso(),
                    "reason": "recovered from interrupted task",
                }
            ]
            queue.append(clone)
    if changed or not QUEUE_FILE.exists():
        atomic_json(QUEUE_FILE, queue)
    return queue


ALLOWED_TASK_KINDS = {
    "validate_project",
    "git_diff_check",
    "stdio_resilience",
    "screenshot_quarantine",
    "controlled_recovery",
    "runtime_observation",
}


def drain_inbox(queue: list[dict[str, Any]]) -> int:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    REJECTED_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    known_ids = {str(task.get("id")) for task in queue if task.get("id")}
    accepted = 0
    for path in sorted(INBOX_DIR.glob("*.json")):
        try:
            task = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(task, dict):
                raise ValueError("task must be a JSON object")
            task_id = str(task.get("id") or "").strip()
            kind = str(task.get("kind") or "").strip()
            if not task_id:
                raise ValueError("task id is required")
            uuid.UUID(task_id)
            if kind not in ALLOWED_TASK_KINDS:
                raise ValueError(f"task kind is not allowed: {kind}")
            if task_id in known_ids:
                path.unlink(missing_ok=True)
                log("inbox_duplicate_discarded", task_id=task_id, kind=kind)
                continue
            task["state"] = "pending"
            task.setdefault("created_at", now_iso())
            task.setdefault("resumable", False)
            task.setdefault("history", []).append(
                {"state": "pending", "at": now_iso(), "reason": "accepted from atomic inbox"}
            )
            queue.append(task)
            atomic_json(QUEUE_FILE, queue)
            known_ids.add(task_id)
            accepted += 1
            path.unlink(missing_ok=True)
            log("inbox_task_accepted", task_id=task_id, kind=kind)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            destination = REJECTED_INBOX_DIR / path.name
            try:
                os.replace(path, destination)
            except OSError:
                pass
            log(
                "inbox_task_rejected",
                file=str(path),
                error_type=type(exc).__name__,
                error=str(exc),
            )
    return accepted


def task_command(task: dict[str, Any]) -> tuple[list[str], dict[str, str], int]:
    kind = str(task.get("kind") or "")
    env = os.environ.copy()
    timeout = int(task.get("timeout_seconds", 600))
    if kind == "validate_project":
        return ([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "validate_project.ps1"), "-ProjectRoot", str(ROOT)], env, min(timeout, 1800))
    if kind == "git_diff_check":
        return (["git", "-C", str(ROOT), "diff", "--check"], env, min(timeout, 120))
    if kind == "stdio_resilience":
        iterations = max(1, min(int(task.get("iterations", 100)), 5000))
        watchdog = "on" if str(task.get("watchdog", "off")).casefold() == "on" else "off"
        return ([str(PYTHON), str(ROOT / "scripts" / "reproduce_stdio_resilience.py"), "--iterations", str(iterations), "--watchdog", watchdog], env, min(timeout, 3600))
    if kind == "screenshot_quarantine":
        env.update({
            "WINDOWS_MCP_SCREENSHOT_TIMEOUT_SECONDS": str(task.get("capture_timeout_seconds", 1)),
            "WINDOWS_MCP_SCREENSHOT_FAILURE_THRESHOLD": "1",
            "WINDOWS_MCP_SCREENSHOT_COOLDOWN_SECONDS": "60",
        })
        return ([str(PYTHON), str(ROOT / "scripts" / "probe_stdio_tool_isolation.py"), "--tool", "Screenshot", "--arguments", '{"use_annotation":false}', "--watchdog", "off", "--repeats", "2", "--expect-error"], env, min(timeout, 180))
    if kind == "controlled_recovery":
        return (
            [
                str(PYTHON),
                str(ROOT / "scripts" / "test_supervisor_recovery.py"),
                "--execute",
                "--project-root",
                str(ROOT),
                "--timeout-seconds",
                str(max(60, min(int(task.get("recovery_timeout_seconds", 180)), 600))),
                "--stability-seconds",
                str(max(15, min(int(task.get("stability_seconds", 30)), 300))),
                "--failure-target",
                "mcp" if str(task.get("failure_target", "tunnel")).casefold() == "mcp" else "tunnel",
            ],
            env,
            min(timeout, 900),
        )
    raise ValueError(f"unsupported task kind: {kind}")


def task_env_overrides(env: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in env.items()
        if key.startswith("WINDOWS_MCP_") and os.environ.get(key) != value
    }


def mark_task_finished(task: dict[str, Any], result: dict[str, Any]) -> None:
    state = str(result.get("state") or "failed")
    exit_code = result.get("exit_code")
    if state == "completed" and exit_code == 0:
        task["state"] = "completed"
    else:
        task["state"] = "failed"
    task["exit_code"] = exit_code
    task["duration_seconds"] = float(result.get("duration_seconds") or 0.0)
    task["completed_at"] = str(result.get("completed_at") or now_iso())
    task["command_pid"] = result.get("command_pid")
    task["timed_out"] = bool(result.get("timed_out"))
    task["terminated_pids"] = result.get("terminated_pids") or []
    error = str(result.get("error") or "")
    if error:
        task["error"] = error
    else:
        task.pop("error", None)
    task.setdefault("history", []).append(
        {"state": task["state"], "at": task["completed_at"]}
    )
    log(
        "task_finished",
        task_id=task.get("id"),
        kind=task.get("kind"),
        state=task["state"],
        exit_code=task.get("exit_code"),
        duration_seconds=task["duration_seconds"],
        timed_out=task["timed_out"],
    )


def launch_command_task(
    task: dict[str, Any],
    queue: list[dict[str, Any]],
) -> None:
    TASK_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    TASK_SPEC_DIR.mkdir(parents=True, exist_ok=True)
    TASK_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    task_id = str(task["id"])
    stdout_path = TASK_EVIDENCE_DIR / f"{task_id}.stdout.log"
    stderr_path = TASK_EVIDENCE_DIR / f"{task_id}.stderr.log"
    spec_path = TASK_SPEC_DIR / f"{task_id}.json"
    result_path = TASK_RESULT_DIR / f"{task_id}.json"
    result_path.unlink(missing_ok=True)
    command, env, timeout = task_command(task)
    spec = {
        "task_id": task_id,
        "command": command,
        "cwd": str(ROOT),
        "timeout_seconds": timeout,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "result_path": str(result_path),
        "env_overrides": task_env_overrides(env),
    }
    atomic_json(spec_path, spec)
    task["state"] = "running"
    task["started_at"] = now_iso()
    task["runner_started_epoch"] = time.time()
    task["spec_file"] = str(spec_path)
    task["result_file"] = str(result_path)
    task["stdout_log"] = str(stdout_path)
    task["stderr_log"] = str(stderr_path)
    task.setdefault("history", []).append(
        {"state": "running", "at": task["started_at"]}
    )
    atomic_json(QUEUE_FILE, queue)
    try:
        runner = subprocess.Popen(
            [str(PYTHON), str(TASK_RUNNER), "--spec", str(spec_path)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
            close_fds=True,
        )
        task["runner_pid"] = runner.pid
        task["runner_started_at"] = now_iso()
        atomic_json(QUEUE_FILE, queue)
        log(
            "task_runner_started",
            task_id=task_id,
            kind=task.get("kind"),
            runner_pid=runner.pid,
            timeout_seconds=timeout,
        )
    except BaseException as exc:
        mark_task_finished(
            task,
            {
                "state": "failed",
                "exit_code": None,
                "duration_seconds": 0.0,
                "completed_at": now_iso(),
                "error": f"{type(exc).__name__}: {exc}",
                "timed_out": False,
                "terminated_pids": [],
            },
        )
        atomic_json(QUEUE_FILE, queue)


def reconcile_command_tasks(queue: list[dict[str, Any]]) -> int:
    finished = 0
    for task in queue:
        if task.get("state") != "running":
            continue
        if task.get("kind") == "runtime_observation":
            continue
        result_value = str(task.get("result_file") or "")
        result_path = Path(result_value) if result_value else None
        if result_path is not None and result_path.is_file():
            result = load_json(result_path, {})
            if isinstance(result, dict) and result.get("state"):
                mark_task_finished(task, result)
                finished += 1
            continue
        runner_pid = task.get("runner_pid")
        if pid_alive(runner_pid):
            continue
        started_epoch = float(task.get("runner_started_epoch") or time.time())
        if time.time() - started_epoch < 10:
            continue
        mark_task_finished(
            task,
            {
                "state": "failed",
                "exit_code": None,
                "duration_seconds": time.time() - started_epoch,
                "completed_at": now_iso(),
                "error": "persisted task runner exited without a result file",
                "timed_out": False,
                "terminated_pids": [],
            },
        )
        finished += 1
    return finished

def sample_observation(task: dict[str, Any], health: dict[str, Any], owned: list[dict[str, Any]], tunnels: list[dict[str, Any]]) -> None:
    if task.get("state") == "pending":
        task["state"] = "running"
        task["started_at"] = now_iso()
        task["started_epoch"] = time.time()
        task["samples"] = 0
        task["healthy_samples"] = 0
        task["failed_samples"] = 0
        task["active_check_seconds"] = 0.0
        task.setdefault("history", []).append({"state": "running", "at": task["started_at"]})
    sample_started = time.perf_counter()
    task["samples"] = int(task.get("samples", 0)) + 1
    sample_ok = bool(health.get("ok")) and len(tunnels) == 1 and bool(owned)
    if sample_ok:
        task["healthy_samples"] = int(task.get("healthy_samples", 0)) + 1
    else:
        task["failed_samples"] = int(task.get("failed_samples", 0)) + 1
    task["last_sample_at"] = now_iso()
    task["last_sample_ok"] = sample_ok
    task["active_check_seconds"] = float(task.get("active_check_seconds", 0.0)) + (time.perf_counter() - sample_started)
    task["observed_wall_seconds"] = max(0.0, time.time() - float(task.get("started_epoch", time.time())))
    target = max(60, int(task.get("target_wall_seconds", 54000)))
    if task["observed_wall_seconds"] >= target:
        task["state"] = "completed" if int(task.get("failed_samples", 0)) == 0 else "failed"
        task["completed_at"] = now_iso()
        task.setdefault("history", []).append({"state": task["state"], "at": task["completed_at"]})
        log("observation_finished", task_id=task.get("id"), state=task["state"], samples=task["samples"], failures=task["failed_samples"])


def process_queue(
    queue: list[dict[str, Any]],
    health: dict[str, Any],
    owned: list[dict[str, Any]],
    tunnels: list[dict[str, Any]],
) -> float:
    started = time.perf_counter()
    reconcile_command_tasks(queue)
    observation = next(
        (
            task
            for task in queue
            if task.get("kind") == "runtime_observation"
            and task.get("state") in {"pending", "running"}
        ),
        None,
    )
    if observation:
        sample_observation(observation, health, owned, tunnels)
    active_command = next(
        (
            task
            for task in queue
            if task.get("kind") != "runtime_observation"
            and task.get("state") == "running"
        ),
        None,
    )
    if active_command is None:
        command_task = next(
            (
                task
                for task in queue
                if task.get("state") == "pending"
                and task.get("kind") != "runtime_observation"
            ),
            None,
        )
        if command_task:
            launch_command_task(command_task, queue)
    atomic_json(QUEUE_FILE, queue)
    return time.perf_counter() - started


def update_checkpoint(state: dict[str, Any], health: dict[str, Any], tunnels: list[dict[str, Any]], owned: list[dict[str, Any]], external: list[dict[str, Any]], queue: list[dict[str, Any]]) -> None:
    checkpoint = load_json(CHECKPOINT, {})
    if not isinstance(checkpoint, dict):
        checkpoint = {}
    checkpoint["updated_at"] = now_iso()
    checkpoint["effective_work_seconds"] = round(float(state.get("effective_work_seconds", 0.0)), 3)
    checkpoint["stage"] = state.get("stage", "runtime_supervision")
    checkpoint["processes"] = {
        "supervisor_pid": os.getpid(),
        "tunnel_pids": [row["pid"] for row in tunnels],
        "tunnel_owned_processes": owned,
        "external_project_processes": external,
    }
    checkpoint["runtime_health"] = health
    checkpoint["queue_summary"] = {
        status: sum(1 for task in queue if task.get("state") == status)
        for status in ("pending", "running", "completed", "failed", "interrupted")
    }
    checkpoint["last_restart_reason"] = state.get("last_restart_reason")
    checkpoint["last_restart_at"] = state.get("last_restart_at")
    checkpoint["next_action"] = state.get("next_action", "Continue from persistent queue and current failure")
    checkpoint["resume_instruction"] = "Supervisor reads queue/state/checkpoint automatically. Verify heartbeat freshness and continue from next_action without repeating approved tests."
    atomic_json(CHECKPOINT, checkpoint)


def main() -> int:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    TASK_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    TASK_SPEC_DIR.mkdir(parents=True, exist_ok=True)
    TASK_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    REJECTED_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    acquire_single_instance()
    while True:
        try:
            queue = recover_queue()
            drain_inbox(queue)
            break
        except TransientPersistenceError as exc:
            log("persistence_retry", path=str(exc.path), error=str(exc))
            time.sleep(1)
    state = load_json(STATE_FILE, {
        "schema_version": 2,
        "started_at": now_iso(),
        "effective_work_seconds": 0.0,
        "restart_history": [],
        "stage": "runtime_supervision_and_persistent_queue",
        "next_action": "Execute persistent verification queue and observe runtime",
    })
    state["schema_version"] = 3
    state["stage"] = "runtime_supervision_transport_aware"
    state["next_action"] = (
        "Verify transport-aware recovery, regression, stress, and long observation"
    )
    profile = ensure_profile_command()
    log(
        "supervisor_started",
        pid=os.getpid(),
        recovered_tasks=len(queue),
        schema_version=3,
        profile=profile,
    )
    try:
        while True:
            try:
                cycle_started = time.perf_counter()
                drain_inbox(queue)
                tunnels = tunnel_processes()
                owned, external = classify_project_processes(tunnels)
                health = read_health(tunnels, owned)
                if len(tunnels) > 1:
                    log(
                        "duplicate_tunnel_detected",
                        pids=[row["pid"] for row in tunnels],
                    )
                    stop_tunnels(tunnels, "duplicate tunnel-client processes detected")
                    state["last_error"] = "duplicate tunnel-client processes removed"
                    state["unhealthy_cycles"] = 0
                elif not tunnels:
                    start_tunnel(state, "tunnel process missing")
                elif not health.get("ok"):
                    state["healthy_cycles"] = 0
                    state["unhealthy_cycles"] = int(
                        state.get("unhealthy_cycles", 0)
                    ) + 1
                    log(
                        "unhealthy",
                        cycles=state["unhealthy_cycles"],
                        health=health,
                    )
                    if (
                        state["unhealthy_cycles"]
                        >= UNHEALTHY_CYCLES_BEFORE_RESTART
                    ):
                        reason = (
                            "transport-aware health failed "
                            f"{state['unhealthy_cycles']} cycles: "
                            f"{health.get('error', '')}"
                        )
                        stop_tunnels(tunnels, reason)
                        time.sleep(2)
                        start_tunnel(state, reason)
                        state["unhealthy_cycles"] = 0
                else:
                    state["unhealthy_cycles"] = 0
                    state["healthy_cycles"] = int(state.get("healthy_cycles", 0)) + 1
                    state["last_healthy_at"] = now_iso()
                    state["last_error"] = ""
                    if (
                        state["healthy_cycles"]
                        >= HEALTHY_CYCLES_TO_RESET_RESTARTS
                    ):
                        state["restart_history"] = []
                queue_seconds = process_queue(queue, health, owned, tunnels)
                active = time.perf_counter() - cycle_started
                state["effective_work_seconds"] = float(state.get("effective_work_seconds", 0.0)) + active
                state["last_cycle_seconds"] = round(active, 4)
                state["last_queue_seconds"] = round(queue_seconds, 4)
                state["last_cycle_at"] = now_iso()
                state["tunnel_count"] = len(tunnels)
                state["owned_process_count"] = len(owned)
                state["external_project_process_count"] = len(external)
                state["health_ok"] = bool(health.get("ok"))
                atomic_json(STATE_FILE, state)
                atomic_json(HEARTBEAT_FILE, {
                    "pid": os.getpid(),
                    "time": now_iso(),
                    "health_ok": bool(health.get("ok")),
                    "tunnel_pids": [row["pid"] for row in tunnels],
                    "tunnel_owned_pids": [row["pid"] for row in owned],
                    "external_project_pids": [row["pid"] for row in external],
                    "effective_work_seconds": round(state["effective_work_seconds"], 3),
                    "queue_summary": {status: sum(1 for task in queue if task.get("state") == status) for status in ("pending", "running", "completed", "failed", "interrupted")},
                })
                update_checkpoint(state, health, tunnels, owned, external, queue)
                time.sleep(POLL_SECONDS)
            except TransientPersistenceError as exc:
                log("persistence_retry", path=str(exc.path), error=str(exc))
                time.sleep(1)
    except KeyboardInterrupt:
        log("supervisor_stopped", reason="keyboard_interrupt")
        return 0
    except BaseException as exc:
        log("supervisor_crashed", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
