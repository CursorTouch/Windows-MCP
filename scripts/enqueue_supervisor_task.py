from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(r"D:\Projetos\WINDOWS-MCP-TEST")
INBOX_DIR = ROOT / ".orquestrador" / "supervisor" / "inbox"
ALLOWED_KINDS = {
    "validate_project",
    "git_diff_check",
    "stdio_resilience",
    "screenshot_quarantine",
    "controlled_recovery",
    "runtime_observation",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        deadline = datetime.now().timestamp() + 5.0
        delay = 0.01
        while True:
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if datetime.now().timestamp() >= deadline:
                    raise
                import time
                time.sleep(delay)
                delay = min(delay * 2, 0.25)
    finally:
        temporary.unlink(missing_ok=True)


def build_task(args: argparse.Namespace) -> dict[str, Any]:
    if args.kind not in ALLOWED_KINDS:
        raise ValueError(f"unsupported task kind: {args.kind}")
    task_id = str(uuid.uuid4())
    task: dict[str, Any] = {
        "id": task_id,
        "kind": args.kind,
        "state": "pending",
        "created_at": now_iso(),
        "timeout_seconds": args.timeout_seconds,
        "resumable": args.resumable,
        "history": [
            {
                "state": "pending",
                "at": now_iso(),
                "reason": args.reason,
            }
        ],
    }
    if args.iterations is not None:
        task["iterations"] = args.iterations
    if args.watchdog is not None:
        task["watchdog"] = args.watchdog
    if args.capture_timeout_seconds is not None:
        task["capture_timeout_seconds"] = args.capture_timeout_seconds
    if args.target_wall_seconds is not None:
        task["target_wall_seconds"] = args.target_wall_seconds
    if args.recovery_timeout_seconds is not None:
        task["recovery_timeout_seconds"] = args.recovery_timeout_seconds
    if args.stability_seconds is not None:
        task["stability_seconds"] = args.stability_seconds
    if args.failure_target is not None:
        task["failure_target"] = args.failure_target
    return task


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Atomically enqueue a supervisor task")
    value.add_argument("kind", choices=sorted(ALLOWED_KINDS))
    value.add_argument("--timeout-seconds", type=int, default=1800)
    value.add_argument("--reason", default="requested through atomic supervisor inbox")
    value.add_argument("--resumable", action="store_true")
    value.add_argument("--iterations", type=int)
    value.add_argument("--watchdog", choices=("on", "off"))
    value.add_argument("--capture-timeout-seconds", type=int)
    value.add_argument("--target-wall-seconds", type=int)
    value.add_argument("--recovery-timeout-seconds", type=int)
    value.add_argument("--stability-seconds", type=int)
    value.add_argument("--failure-target", choices=("tunnel", "mcp"))
    return value


def main() -> int:
    args = parser().parse_args()
    task = build_task(args)
    destination = INBOX_DIR / f"{task['id']}.json"
    atomic_json(destination, task)
    print(json.dumps({"queued": True, "path": str(destination), "task": task}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
