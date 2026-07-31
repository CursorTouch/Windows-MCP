from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _exception_details(exc: BaseException) -> dict[str, Any]:
    details: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": str(exc),
    }
    if isinstance(exc, BaseExceptionGroup):
        details["exceptions"] = [_exception_details(item) for item in exc.exceptions]
    if exc.__cause__ is not None:
        details["cause"] = _exception_details(exc.__cause__)
    if exc.__context__ is not None and exc.__context__ is not exc.__cause__:
        details["context"] = _exception_details(exc.__context__)
    return details


async def run_probe(root: Path, iterations: int, timeout_seconds: int, watchdog_mode: str, concurrency: int = 1) -> dict[str, Any]:
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise FileNotFoundError(f"project Python not found: {python}")

    evidence_dir = root / ".orquestrador" / "evidencias" / "stdio-probe"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stderr_path = evidence_dir / f"stdio-stderr-{stamp}.log"

    env = os.environ.copy()
    env.update(
        {
            "WINDOWS_MCP_WATCHDOG": watchdog_mode,
            "WINDOWS_MCP_PROJECT_ROOT": str(root),
            "ANONYMIZED_TELEMETRY": "false",
            "NO_COLOR": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    params = StdioServerParameters(
        command=str(python),
        args=["-m", "windows_mcp", "serve", "--transport", "stdio"],
        env=env,
        cwd=root,
        encoding="utf-8",
        encoding_error_handler="replace",
    )

    result: dict[str, Any] = {
        "schema_version": 1,
        "started_at_epoch": time.time(),
        "root": str(root),
        "iterations_requested": iterations,
        "timeout_seconds": timeout_seconds,
        "watchdog_mode": watchdog_mode,
        "concurrency": concurrency,
        "stderr_log": str(stderr_path),
        "steps": [],
        "passed": False,
    }
    started = time.perf_counter()

    with stderr_path.open("w", encoding="utf-8", errors="replace") as errlog:
        try:
            async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=timeout_seconds),
                ) as session:
                    initialized = await session.initialize()
                    result["steps"].append(
                        {"step": "initialize", "ok": True, "result": _jsonable(initialized)}
                    )

                    listed = await session.list_tools()
                    tool_names = [tool.name for tool in listed.tools]
                    result["tool_count"] = len(tool_names)
                    result["tools"] = tool_names
                    result["steps"].append(
                        {"step": "list_tools", "ok": True, "count": len(tool_names)}
                    )

                    required = {"Health", "SystemQuery", "SafetyDryRun"}
                    missing = sorted(required.difference(tool_names))
                    if missing:
                        raise RuntimeError(f"required tools missing: {missing}")

                    health_before = await session.call_tool("Health", {})
                    result["steps"].append(
                        {
                            "step": "health_before_rejection",
                            "ok": not bool(health_before.isError),
                            "result": _jsonable(health_before),
                        }
                    )
                    if health_before.isError:
                        raise RuntimeError("Health failed before rejection probe")

                    allowed = await session.call_tool("SafetyDryRun", {"probe": "date_time"})
                    result["steps"].append(
                        {
                            "step": "safe_probe_allowed",
                            "ok": not bool(allowed.isError),
                            "result": _jsonable(allowed),
                        }
                    )
                    if allowed.isError:
                        raise RuntimeError("safe SafetyDryRun probe was rejected")

                    denied = await session.call_tool("SafetyDryRun", {"probe": "disk_format"})
                    result["steps"].append(
                        {
                            "step": "dangerous_probe_rejected",
                            "ok": bool(denied.isError),
                            "result": _jsonable(denied),
                        }
                    )
                    if not denied.isError:
                        raise RuntimeError("dangerous SafetyDryRun probe was not rejected")

                    health_after = await session.call_tool("Health", {})
                    result["steps"].append(
                        {
                            "step": "health_after_rejection",
                            "ok": not bool(health_after.isError),
                            "result": _jsonable(health_after),
                        }
                    )
                    if health_after.isError:
                        raise RuntimeError("session did not survive rejected tool call")

                    query = await session.call_tool(
                        "SystemQuery", {"operation": "date_time", "limit": 1}
                    )
                    result["steps"].append(
                        {
                            "step": "system_query_after_rejection",
                            "ok": not bool(query.isError),
                            "result": _jsonable(query),
                        }
                    )
                    if query.isError:
                        raise RuntimeError("SystemQuery failed after rejected tool call")

                    concurrency = max(1, min(int(concurrency), 64))
                    completed = 0
                    for start_index in range(0, iterations, concurrency):
                        batch_size = min(concurrency, iterations - start_index)
                        responses = await asyncio.gather(
                            *(session.call_tool("Health", {}) for _ in range(batch_size))
                        )
                        if any(response.isError for response in responses):
                            raise RuntimeError(
                                f"Health concurrent batch at {start_index + 1} returned isError"
                            )
                        completed += len(responses)
                    result["health_calls_completed"] = completed
                    result["steps"].append(
                        {
                            "step": "concurrent_health_calls",
                            "ok": completed == iterations,
                            "completed": completed,
                            "concurrency": concurrency,
                        }
                    )

                    mixed_count = min(max(6, concurrency), 32)
                    mixed_calls = []
                    for index in range(mixed_count):
                        if index % 3 == 0:
                            mixed_calls.append(session.call_tool("Health", {}))
                        elif index % 3 == 1:
                            mixed_calls.append(
                                session.call_tool("SystemQuery", {"operation": "date_time"})
                            )
                        else:
                            mixed_calls.append(
                                session.call_tool("SystemQuery", {"operation": "git_status"})
                            )
                    mixed_responses = await asyncio.gather(*mixed_calls)
                    mixed_errors = sum(bool(response.isError) for response in mixed_responses)
                    if mixed_errors:
                        raise RuntimeError(
                            f"mixed concurrent calls returned {mixed_errors} errors"
                        )
                    result["steps"].append(
                        {
                            "step": "mixed_concurrent_calls",
                            "ok": True,
                            "completed": len(mixed_responses),
                        }
                    )
                    result["passed"] = True
        except BaseException as exc:
            result["error_type"] = type(exc).__name__
            result["error"] = str(exc)
            result["error_details"] = _exception_details(exc)

    result["duration_seconds"] = round(time.perf_counter() - started, 3)
    result["completed_at_epoch"] = time.time()
    result_path = evidence_dir / f"stdio-result-{stamp}.json"
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"D:\Projetos\WINDOWS-MCP-TEST")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--watchdog", choices=["on", "off"], default="off")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()

    result = asyncio.run(run_probe(Path(args.root).resolve(), args.iterations, args.timeout, args.watchdog, args.concurrency))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
