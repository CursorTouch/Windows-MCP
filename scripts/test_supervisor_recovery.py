from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + 5.0
        delay = 0.025
        while True:
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.5)
    finally:
        temporary.unlink(missing_ok=True)


def read_text(url: str, timeout: float = 3.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace").strip()


def read_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    value = json.loads(read_text(url, timeout=timeout))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object from {url}")
    return value


def command_line(proc: psutil.Process) -> str:
    try:
        return " ".join(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return ""


def tunnel_processes(profile_name: str) -> list[psutil.Process]:
    found: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = str(proc.info.get("name") or "").casefold()
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if name == "tunnel-client.exe" and profile_name in cmdline:
                found.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return sorted(found, key=lambda proc: proc.pid)


def runtime_state(health_file: Path, expected_command: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "base_url": "",
        "health": "",
        "ready": "",
        "command": "",
        "mcp_pid": 0,
        "probe": "",
        "error": "",
    }
    try:
        if not health_file.is_file():
            raise FileNotFoundError("health URL file missing")
        base = health_file.read_text(encoding="utf-8-sig").strip().rstrip("/")
        if not base.startswith("http://127.0.0.1:"):
            raise ValueError("health URL is outside loopback")
        status = read_json(f"{base}/api/status")
        system = read_json(f"{base}/api/system")
        live = read_text(f"{base}/healthz")
        ready = read_text(f"{base}/readyz")
        channels = status.get("channels")
        if not isinstance(channels, list):
            raise ValueError("status channels missing")
        main = next(
            (item for item in channels if isinstance(item, dict) and item.get("name") == "main"),
            None,
        )
        if not isinstance(main, dict):
            raise ValueError("main channel missing")
        details = {
            str(item.get("key")): str(item.get("value"))
            for item in main.get("details", [])
            if isinstance(item, dict) and item.get("key") is not None
        }
        mcp_pid = int(details.get("pid", "0"))
        command = details.get("command", "")
        probe = str(system.get("main_channel_probe_status") or "")
        process_ok = mcp_pid > 0 and psutil.pid_exists(mcp_pid)
        result.update(
            {
                "base_url": base,
                "health": live,
                "ready": ready,
                "command": command,
                "mcp_pid": mcp_pid,
                "probe": probe,
                "ok": bool(
                    live == "live"
                    and ready == "ready"
                    and command == expected_command
                    and probe == "ok"
                    and process_ok
                ),
            }
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        urllib.error.URLError,
        TimeoutError,
    ) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def process_tree(root_pid: int, limit: int = 256) -> list[dict[str, Any]]:
    snapshot: dict[int, dict[str, Any]] = {}
    children: dict[int, list[int]] = {}
    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
        try:
            pid = int(proc.info["pid"])
            ppid = int(proc.info.get("ppid") or 0)
            row = {
                "name": str(proc.info.get("name") or ""),
                "pid": pid,
                "ppid": ppid,
                "command_line": " ".join(proc.info.get("cmdline") or []),
            }
            snapshot[pid] = row
            children.setdefault(ppid, []).append(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    rows: list[dict[str, Any]] = []
    pending = [root_pid]
    visited: set[int] = set()
    while pending and len(visited) < limit:
        pid = pending.pop(0)
        if pid in visited:
            continue
        visited.add(pid)
        row = snapshot.get(pid)
        if row is not None:
            rows.append(row)
        pending.extend(child for child in children.get(pid, []) if child not in visited)
    return rows


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Bounded automatic tunnel recovery proof")
    value.add_argument("--execute", action="store_true")
    value.add_argument("--project-root", default=r"D:\Projetos\WINDOWS-MCP-TEST")
    value.add_argument("--profile-name", default="windows-mcp-gpt-managed")
    value.add_argument("--timeout-seconds", type=int, default=180)
    value.add_argument("--stability-seconds", type=int, default=30)
    return value


def main() -> int:
    args = parser().parse_args()
    root = Path(args.project_root).resolve()
    guard_dir = root / ".orquestrador" / "evidencias" / "guards"
    if not args.execute:
        guard_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "script": str(Path(__file__).resolve()),
            "status": "SKIPPED_NO_EXECUTE",
            "timestamp": now_iso(),
        }
        with (guard_dir / "destructive-task-guard.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False))
        return 0

    timeout_seconds = max(60, min(int(args.timeout_seconds), 600))
    stability_seconds = max(15, min(int(args.stability_seconds), 300))
    started_wall = datetime.now().astimezone()
    started = time.monotonic()
    evidence_dir = root / ".orquestrador" / "evidencias" / "recovery"
    evidence_path = evidence_dir / f"supervisor-recovery-{started_wall:%Y%m%d-%H%M%S}.json"
    health_file = Path.home() / ".local" / "state" / "tunnel-client" / "health" / "windows-mcp-gpt.url"
    heartbeat_file = root / ".orquestrador" / "supervisor" / "heartbeat.json"
    expected_command = f"{root.as_posix()}/.venv/Scripts/python.exe -m windows_mcp serve --transport stdio"
    result: dict[str, Any] = {
        "started_at": started_wall.isoformat(),
        "old_tunnel_pid": None,
        "new_tunnel_pid": None,
        "recovery_seconds": None,
        "recovered": False,
        "stable": False,
        "stability_samples": 0,
        "max_tunnel_count": 0,
        "duplicate_samples": 0,
        "runtime": None,
        "heartbeat": None,
        "process_tree": [],
        "error": "",
    }
    exit_code = 1
    try:
        before = tunnel_processes(args.profile_name)
        if len(before) != 1:
            raise RuntimeError(f"expected one tunnel before test, found {len(before)}")
        old_pid = before[0].pid
        result["old_tunnel_pid"] = old_pid
        time.sleep(3)
        psutil.Process(old_pid).kill()
        try:
            psutil.Process(old_pid).wait(timeout=5)
        except psutil.TimeoutExpired:
            pass

        recovery_deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < recovery_deadline:
            tunnels = tunnel_processes(args.profile_name)
            result["max_tunnel_count"] = max(result["max_tunnel_count"], len(tunnels))
            if len(tunnels) > 1:
                result["duplicate_samples"] += 1
            runtime = runtime_state(health_file, expected_command)
            if len(tunnels) == 1 and tunnels[0].pid != old_pid and runtime["ok"]:
                result["new_tunnel_pid"] = tunnels[0].pid
                result["runtime"] = runtime
                result["recovery_seconds"] = round(time.monotonic() - started - 3, 3)
                result["recovered"] = True
                break
            time.sleep(1)
        if not result["recovered"]:
            raise RuntimeError("supervisor did not restore a healthy tunnel within timeout")

        stable_until = time.monotonic() + stability_seconds
        stable = True
        while time.monotonic() < stable_until:
            tunnels = tunnel_processes(args.profile_name)
            result["stability_samples"] += 1
            result["max_tunnel_count"] = max(result["max_tunnel_count"], len(tunnels))
            if len(tunnels) > 1:
                result["duplicate_samples"] += 1
            runtime = runtime_state(health_file, expected_command)
            if (
                len(tunnels) != 1
                or tunnels[0].pid != result["new_tunnel_pid"]
                or not runtime["ok"]
            ):
                stable = False
            time.sleep(1)
        result["stable"] = stable
        if heartbeat_file.is_file():
            result["heartbeat"] = json.loads(heartbeat_file.read_text(encoding="utf-8-sig"))
        result["process_tree"] = process_tree(int(result["new_tunnel_pid"]))
        if not stable:
            raise RuntimeError("recovered tunnel did not remain stable during observation")
        if result["duplicate_samples"]:
            raise RuntimeError("duplicate tunnel process detected during recovery")
        exit_code = 0
    except (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        psutil.Error,
    ) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["completed_at"] = now_iso()
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    result["passed"] = bool(
        exit_code == 0
        and result["recovered"]
        and result["stable"]
        and result["duplicate_samples"] == 0
        and not result["error"]
    )
    atomic_json(evidence_path, result)
    result["evidence_path"] = str(evidence_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
