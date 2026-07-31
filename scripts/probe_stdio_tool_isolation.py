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


def summarize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key == "data" and isinstance(item, str):
                result[key] = {"length": len(item), "omitted": True}
            else:
                result[str(key)] = summarize(item)
        return result
    if isinstance(value, list):
        return [summarize(item) for item in value]
    if isinstance(value, str) and len(value) > 2000:
        return {"preview": value[:500], "length": len(value), "truncated": True}
    return value


async def run(root: Path, tool: str, arguments: dict[str, Any], watchdog: str, repeats: int, expect_error: bool = False) -> dict[str, Any]:
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise FileNotFoundError(f"project Python not found: {python}")
    evidence_dir = root / ".orquestrador" / "evidencias" / "tool-isolation"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_tool = "".join(ch if ch.isalnum() else "-" for ch in tool)
    stderr_path = evidence_dir / f"{safe_tool}-{watchdog}-{stamp}.stderr.log"
    result_path = evidence_dir / f"{safe_tool}-{watchdog}-{stamp}.json"

    env = os.environ.copy()
    env.update({
        "WINDOWS_MCP_WATCHDOG": watchdog,
        "WINDOWS_MCP_PROJECT_ROOT": str(root),
        "ANONYMIZED_TELEMETRY": "false",
        "NO_COLOR": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    params = StdioServerParameters(
        command=str(python),
        args=["-m", "windows_mcp", "serve", "--transport", "stdio"],
        env=env,
        cwd=root,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": tool,
        "arguments": arguments,
        "watchdog": watchdog,
        "repeats": repeats,
        "expect_error": expect_error,
        "stderr_log": str(stderr_path),
        "calls": [],
        "passed": False,
    }
    started = time.perf_counter()
    with stderr_path.open("w", encoding="utf-8", errors="replace") as errlog:
        try:
            async with stdio_client(params, errlog=errlog) as streams:
                async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=30)) as session:
                    await session.initialize()
                    before = await session.call_tool("Health", {})
                    if before.isError:
                        raise RuntimeError("Health failed before target tool")
                    report["health_before"] = summarize(before)
                    for index in range(repeats):
                        response = await session.call_tool(tool, arguments, read_timeout_seconds=timedelta(seconds=30))
                        report["calls"].append({
                            "index": index + 1,
                            "is_error": bool(response.isError),
                            "result": summarize(response),
                        })
                        if expect_error and not response.isError:
                            raise RuntimeError(f"{tool} was expected to fail on call {index + 1}")
                        if not expect_error and response.isError:
                            raise RuntimeError(f"{tool} returned isError on call {index + 1}")
                    after = await session.call_tool("Health", {})
                    report["health_after"] = summarize(after)
                    if after.isError:
                        raise RuntimeError("Health failed after target tool")
                    report["passed"] = True
        except BaseException as exc:
            report["error_type"] = type(exc).__name__
            report["error"] = str(exc)
    report["duration_seconds"] = round(time.perf_counter() - started, 3)
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["result_path"] = str(result_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"D:\Projetos\WINDOWS-MCP-TEST")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--arguments", default="{}")
    parser.add_argument("--watchdog", choices=["on", "off"], default="off")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--expect-error", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(run(Path(args.root).resolve(), args.tool, json.loads(args.arguments), args.watchdog, args.repeats, args.expect_error))
    print(json.dumps({
        "tool": report["tool"],
        "watchdog": report["watchdog"],
        "repeats": report["repeats"],
        "passed": report["passed"],
        "duration_seconds": report["duration_seconds"],
        "error_type": report.get("error_type"),
        "error": report.get("error"),
        "result_path": report["result_path"],
        "stderr_log": report["stderr_log"],
    }, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
