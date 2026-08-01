from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + 5.0
        delay = 0.01
        while True:
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.25)
    finally:
        temporary.unlink(missing_ok=True)


def read_json_retry(path: Path, timeout_seconds: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    delay = 0.01
    while True:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("JSON document must be an object")
            return value
        except FileNotFoundError:
            raise
        except (PermissionError, json.JSONDecodeError, OSError) as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"failed to read JSON after retries: {path}: {exc}") from exc
            time.sleep(delay)
            delay = min(delay * 2, 0.25)


def terminate_tree(pid: int) -> list[int]:
    terminated: list[int] = []
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return terminated
    children = root.children(recursive=True)
    for process in reversed(children):
        try:
            process.terminate()
            terminated.append(process.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    try:
        root.terminate()
        terminated.append(root.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    _, alive = psutil.wait_procs([*children, root], timeout=5)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return terminated


def run(spec: dict[str, Any]) -> dict[str, Any]:
    command = spec.get("command")
    if not isinstance(command, list) or not command:
        raise ValueError("command must be a non-empty list")
    if not all(isinstance(item, str) and item for item in command):
        raise ValueError("command contains an invalid argument")
    cwd = Path(str(spec.get("cwd") or ""))
    if not cwd.is_dir():
        raise ValueError(f"working directory does not exist: {cwd}")
    timeout = max(1, int(spec.get("timeout_seconds", 600)))
    stdout_path = Path(str(spec["stdout_path"]))
    stderr_path = Path(str(spec["stderr_path"]))
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    overrides = spec.get("env_overrides")
    if isinstance(overrides, dict):
        env.update({str(key): str(value) for key, value in overrides.items()})

    started = time.perf_counter()
    result: dict[str, Any] = {
        "task_id": str(spec.get("task_id") or ""),
        "runner_pid": os.getpid(),
        "started_at": now_iso(),
        "state": "failed",
        "exit_code": None,
        "error": "",
        "timed_out": False,
        "terminated_pids": [],
    }
    process: subprocess.Popen[Any] | None = None
    try:
        with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout, stderr_path.open(
            "w",
            encoding="utf-8",
            errors="replace",
        ) as stderr:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=CREATE_NO_WINDOW,
                close_fds=True,
            )
            result["command_pid"] = process.pid
            try:
                exit_code = process.wait(timeout=timeout)
                result["exit_code"] = exit_code
                result["state"] = "completed" if exit_code == 0 else "failed"
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                result["error"] = f"timeout after {timeout}s"
                result["terminated_pids"] = terminate_tree(process.pid)
                result["exit_code"] = process.poll()
    except BaseException as exc:
        if process is not None and process.poll() is None:
            result["terminated_pids"] = terminate_tree(process.pid)
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["state"] = "failed"
    result["duration_seconds"] = round(time.perf_counter() - started, 3)
    result["completed_at"] = now_iso()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one persisted supervisor task")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--result")
    args = parser.parse_args()
    spec_path = Path(args.spec).resolve()
    result_path = Path(args.result).resolve() if args.result else None
    spec: dict[str, Any] = {}
    started_at = now_iso()
    try:
        spec = read_json_retry(spec_path)
        declared_result = Path(str(spec["result_path"])).resolve()
        if result_path is not None and declared_result != result_path:
            raise ValueError(
                f"result path mismatch: argument={result_path} spec={declared_result}"
            )
        result_path = declared_result
        result = run(spec)
    except BaseException as exc:
        result = {
            "task_id": str(spec.get("task_id") or ""),
            "runner_pid": os.getpid(),
            "started_at": started_at,
            "completed_at": now_iso(),
            "duration_seconds": 0.0,
            "state": "failed",
            "exit_code": None,
            "error": f"{type(exc).__name__}: {exc}",
            "timed_out": False,
            "terminated_pids": [],
        }
    if result_path is None:
        raise RuntimeError(
            "result path unavailable; pass --result so startup failures can be persisted"
        )
    atomic_json(result_path, result)
    return 0 if result.get("state") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
