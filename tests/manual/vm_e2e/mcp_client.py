"""In-VM MCP test client.

Connects to a `windows-mcp serve` process (stdio transport) and exercises the
secure-desktop tool surface end-to-end. Writes a JSON results file the host
side can read off the bind-mount share.

Usage (run from inside the Windows VM):

    python mcp_client.py --results C:\\path\\to\\results.json [--http URL]

When --http is given, talks to a remote server over streamable-http instead
of spawning the local stdio server. That mode is for the Linux-side driver
in path B.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

try:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamablehttp_client
except ImportError as exc:
    raise SystemExit(
        f"mcp client SDK not importable: {exc}\n"
        "Inside the venv used to install windows-mcp, run: pip install mcp"
    )


@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""
    duration_s: float = 0.0


@dataclass
class Report:
    started_at: str = ""
    finished_at: str = ""
    transport: str = ""
    results: list[TestResult] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


async def run_tool(session: ClientSession, name: str, args: dict | None = None) -> Any:
    args = args or {}
    result = await session.call_tool(name, args)
    return result


async def assert_service_running(session: ClientSession, report: Report) -> None:
    start = time.monotonic()
    # We assert via a tool that exists in the broker — the broker is what
    # owns the pipe client. The Snapshot tool will pull a screenshot through
    # the service when secure desktop is active; here we just hit any tool
    # so we know MCP plumbing works.
    try:
        tools = await session.list_tools()
        names = [t.name for t in tools.tools]
        ok = "WaitForUACPrompt" in names
        detail = f"tools: {sorted(names)[:10]}…"
    except Exception as exc:
        ok, detail = False, f"list_tools failed: {exc}"
    report.results.append(TestResult(
        "list_tools includes WaitForUACPrompt", ok, detail, time.monotonic() - start,
    ))


async def assert_wait_for_uac_returns_dialog(
    session: ClientSession, report: Report
) -> None:
    """Trigger UAC, expect WaitForUACPrompt to return the dialog."""
    start = time.monotonic()
    # Spawn an elevation prompt asynchronously so it arrives while we wait.
    trigger = subprocess.Popen(
        [
            "powershell.exe", "-NoLogo", "-NoProfile", "-Command",
            "Start-Sleep -Milliseconds 1500; "
            "Start-Process -FilePath cmd.exe -Verb RunAs -WindowStyle Hidden",
        ],
    )
    try:
        result = await run_tool(
            session, "WaitForUACPrompt", {"timeout_ms": 30_000}
        )
        # FastMCP returns a structured response; pull the first text content
        payload = _extract_payload(result)
        ok = bool(payload.get("ok")) and payload.get("fired") is True
        if ok:
            tree = payload.get("tree") or []
            ok = bool(tree)
            detail = (
                f"publisher={payload.get('publisher')!r} "
                f"top_windows={len(tree)} "
                f"policy={payload.get('policy')}"
            )
        else:
            detail = f"payload: {json.dumps(payload)[:300]}"
    except Exception as exc:
        ok, detail = False, f"call failed: {exc}"
    finally:
        try:
            trigger.wait(timeout=5)
        except Exception:
            trigger.kill()
    report.results.append(TestResult(
        "WaitForUACPrompt returns dialog after UAC fires", ok, detail,
        time.monotonic() - start,
    ))


def _extract_payload(call_result: Any) -> dict:
    """Pull the JSON payload out of an MCP call_tool result."""
    content = getattr(call_result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text is None:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}
    return {}


async def run_async(args: argparse.Namespace) -> Report:
    report = Report(
        started_at=_now(),
        transport=("http" if args.http else "stdio"),
    )
    if args.http:
        async with streamablehttp_client(args.http) as (read, write, _info):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await _run_suite(session, report)
    else:
        params = StdioServerParameters(
            command=args.python or sys.executable,
            args=["-m", "windows_mcp", "serve", "--transport", "stdio"],
            env=os.environ.copy(),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await _run_suite(session, report)
    report.finished_at = _now()
    report.summary = {
        "total": len(report.results),
        "passed": sum(1 for r in report.results if r.passed),
        "failed": sum(1 for r in report.results if not r.passed),
    }
    return report


async def _run_suite(session: ClientSession, report: Report) -> None:
    await assert_service_running(session, report)
    await assert_wait_for_uac_returns_dialog(session, report)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, help="Write JSON report here.")
    ap.add_argument("--http", help="Talk to a remote server at this URL instead of stdio.")
    ap.add_argument("--python", help="Override the python.exe used for the stdio server.")
    args = ap.parse_args()

    try:
        report = asyncio.run(run_async(args))
    except Exception as exc:
        report = Report(
            started_at=_now(), finished_at=_now(), transport="(failed to start)",
            results=[TestResult("driver bootstrap", False, repr(exc), 0.0)],
            summary={"total": 1, "passed": 0, "failed": 1},
        )

    with open(args.results, "w", encoding="utf-8") as fh:
        json.dump({
            **asdict(report),
            "results": [asdict(r) for r in report.results],
        }, fh, indent=2, default=str)

    print(json.dumps(report.summary, indent=2))
    return 0 if report.summary.get("failed", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
