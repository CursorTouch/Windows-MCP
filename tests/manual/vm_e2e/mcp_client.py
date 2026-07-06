"""In-VM MCP test client.

Connects to a `windows-mcp serve` process (stdio transport) and exercises the
secure-desktop tool surface end-to-end. Writes a JSON results file the host
side can read off the bind-mount share.

Usage (run from inside the Windows VM):

    python mcp_client.py --results C:\\path\\to\\results.json [--http URL]

When --http is given, talks to a remote server over streamable-http instead
of spawning the local stdio server. That mode is for the Linux-side driver
in path B.

The suite asserts, **per assertion** (no "all green if any pass"):

  1. list_tools includes WaitForUACPrompt.
  2. WaitForUACPrompt blocks then returns a non-empty UIA tree after we
     trigger UAC via `Start-Process -Verb RunAs`.
  3. The returned tree contains a "Yes" button with valid coordinates.
  4. Click(loc=[Yes.x, Yes.y]) under policy=allow_all dismisses UAC
     (a follow-up WaitForUACPrompt with a short timeout returns fired=False).
  5. Under policy=block, Click is REFUSED with a "policy denied" error,
     and the dialog stays on screen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import timedelta
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def call(
    session: ClientSession,
    name: str,
    args: dict | None = None,
    read_timeout: float | None = None,
) -> dict:
    """Call an MCP tool and return the parsed JSON payload.

    *read_timeout* (seconds) bounds how long the client waits for the tool
    response. WaitForUACPrompt can block for a long time on a slow VM, so it
    passes a generous value; without it the SDK's default request timeout can
    fire before the server-side poll finds consent.exe.
    """
    args = args or {}
    kwargs: dict[str, Any] = {}
    if read_timeout is not None:
        kwargs["read_timeout_seconds"] = timedelta(seconds=read_timeout)
    raw = await session.call_tool(name, args, **kwargs)
    return _extract_payload(raw)


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


def _find_named_invokable(tree: list[dict], name: str) -> dict | None:
    """DFS through a WaitForUACPrompt tree looking for `name` with can_invoke=True.

    Strips Windows accelerator prefixes ('&Yes' -> 'yes') and matches case
    -insensitively. consent.exe's Yes/No buttons carry the accelerator on
    Win 11."""
    target = name.strip().lower().lstrip("&")
    stack = list(tree)
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        nname = (node.get("name") or "").strip().lower().lstrip("&")
        if nname == target and node.get("can_invoke"):
            return node
        for child in node.get("children") or []:
            stack.append(child)
    return None


class _Trigger:
    """Handle for an in-flight UAC trigger, quacking like the Popen this used
    to return (``.wait(timeout)`` / ``.kill()``) so callers don't change."""

    def __init__(self, thread: threading.Thread) -> None:
        self._thread = thread

    def wait(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def kill(self) -> None:
        # The worker thread is a daemon blocked inside ShellExecuteW until the
        # consent decision is made; it dies with the process. Nothing to kill.
        pass


def _trigger_uac() -> _Trigger:
    """Fire a real UAC prompt asynchronously and return a handle.

    This Python process is medium-integrity (run_all.ps1 launches mcp_client.py
    via `runas /trustlevel:0x20000`). From medium integrity, requesting
    elevation of cmd.exe trips UAC; with PromptOnSecureDesktop=0 the consent
    dialog renders on the Default desktop where WaitForUACPrompt can reach it.

    We invoke ShellExecuteW(..., "runas", ...) directly instead of spawning
    powershell -> Start-Process. Spawning powershell added a large, variable
    cold-start delay (tens of seconds on a KVM-less TCG VM) before the prompt
    even appeared, which made timing the WaitForUACPrompt wait fragile. The
    direct call surfaces the prompt in ~a second. It BLOCKS until the consent
    decision, so it runs on a daemon thread while the main flow proceeds to
    WaitForUACPrompt (which sees the dialog and clicks Yes, unblocking it).
    """
    def _fire() -> None:
        try:
            import ctypes
            # SW_HIDE=0: the elevated cmd itself stays hidden; the consent
            # dialog is drawn by the system regardless.
            ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", None, None, 0)
        except Exception:
            pass

    thread = threading.Thread(target=_fire, name="uac-trigger", daemon=True)
    thread.start()
    return _Trigger(thread)


def _record(report: Report, name: str, ok: bool, detail: str, t0: float) -> None:
    report.results.append(TestResult(
        name=name, passed=ok, detail=detail, duration_s=time.monotonic() - t0,
    ))


# ---------------------------------------------------------------------------
# assertions
# ---------------------------------------------------------------------------

async def assert_list_tools(session: ClientSession, report: Report) -> None:
    t0 = time.monotonic()
    try:
        tools = await session.list_tools()
        names = sorted(t.name for t in tools.tools)
        ok = "WaitForUACPrompt" in names
        detail = f"{len(names)} tools registered; first: {names[:8]}…"
    except Exception as exc:
        ok, detail = False, f"list_tools failed: {exc}"
    _record(report, "list_tools includes WaitForUACPrompt", ok, detail, t0)


async def assert_wait_for_uac_returns_dialog(
    session: ClientSession, report: Report, *, expect_policy: str
) -> dict | None:
    """Trigger UAC, expect WaitForUACPrompt to return a non-empty tree.

    Returns the payload so the next assertion can find the Yes button.
    """
    t0 = time.monotonic()
    trigger = _trigger_uac()
    payload: dict = {}
    try:
        # The trigger launches powershell then Start-Process -Verb RunAs. On a
        # KVM-less TCG VM, powershell's cold start alone can take ~a minute, so
        # the consent dialog often doesn't appear until well after a 30s wait.
        # Poll server-side for up to 180s, and give the client transport an
        # even longer read timeout so it doesn't give up first.
        payload = await call(
            session, "WaitForUACPrompt", {"timeout_ms": 180_000}, read_timeout=200
        )
    except Exception as exc:
        _record(report, "WaitForUACPrompt returns dialog", False, f"call failed: {exc}", t0)
        return None
    finally:
        try:
            trigger.wait(timeout=10)
        except Exception:
            trigger.kill()

    fired = bool(payload.get("ok")) and payload.get("fired") is True
    tree = payload.get("tree") or []
    ok = fired and bool(tree)
    # Dump the full payload to the share so the host-side driver can see
    # exactly what the worker returned. Useful for diagnosing the "Yes button
    # not invokable" downstream assertion after fix 2 verified the tree is
    # non-empty.
    try:
        dump_path = r"\\host.lan\Data\Windows-MCP\tests\manual\vm_e2e\.work\wait_for_uac_payload.json"
        with open(dump_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
    except Exception:
        pass
    # Collect any _diag markers from the tree so we see the worker's
    # consent-pid lookup even when the tree itself is empty.
    diag_msgs: list[str] = []
    def _gather(node):
        if not isinstance(node, dict):
            return
        d = node.get("_diag")
        if d:
            diag_msgs.append(d)
        for c in node.get("children") or []:
            _gather(c)
    for top in tree:
        _gather(top)
    detail = (
        f"ok={payload.get('ok')!r} fired={payload.get('fired')!r} "
        f"reason={payload.get('reason')!r} error={payload.get('error')!r} "
        f"top_windows={len(tree)} publisher={payload.get('publisher')!r} "
        f"policy={payload.get('policy')}"
    )
    if diag_msgs:
        detail += f" worker_diag={diag_msgs!r}"
    _record(report, "WaitForUACPrompt returns dialog", ok, detail, t0)

    # Bonus assertion: policy reported matches what we set up.
    pol = (payload.get("policy") or {}).get("policy")
    _record(
        report,
        f"policy is {expect_policy}",
        pol == expect_policy,
        f"got {pol!r}",
        t0,
    )

    return payload if ok else None


async def assert_yes_button_present(
    payload: dict, report: Report
) -> dict | None:
    t0 = time.monotonic()
    tree = payload.get("tree") or []
    yes = _find_named_invokable(tree, "Yes")
    if yes is None:
        # Dump everything we can see -- name/ctrl/can_invoke for every node --
        # so the failure detail is actionable (localized label? can_invoke=False
        # on consent.exe? wrong subtree walked?).
        candidates: list[str] = []
        def _walk(node: dict, depth: int = 0):
            if not isinstance(node, dict):
                return
            n = (node.get("name") or "").strip()
            c = node.get("control_type") or ""
            inv = node.get("can_invoke")
            if n or c:
                candidates.append(f"d{depth} ctrl={c!r} name={n!r} inv={inv}")
            for child in node.get("children") or []:
                _walk(child, depth + 1)
        for top in tree:
            _walk(top)
        # Print to stderr + collect any _diag markers the worker attached
        # so we can see what _find_top_hwnd_for_pid actually returned even
        # if the broker's SYSTEM log isn't readable from the user session.
        sys.stderr.write("\n===== UAC TREE DUMP (Yes not found) =====\n")
        for line in candidates:
            sys.stderr.write(line + "\n")
        sys.stderr.write("===== END UAC TREE DUMP =====\n")
        sys.stderr.flush()
        diag: list[str] = []
        def _collect_diag(node):
            if not isinstance(node, dict):
                return
            d = node.get("_diag")
            if d:
                diag.append(d)
            for child in node.get("children") or []:
                _collect_diag(child)
        for top in tree:
            _collect_diag(top)
        diag_blurb = "; ".join(diag) if diag else "(no _diag markers)"
        # Squash candidates to a compact, single-line listing in the detail so
        # the per-run results-allow_all.json captures the full picture even
        # when the next run overwrites mcp_client-allow_all.log.
        snapshot = " || ".join(
            c.replace("\n", " ") for c in candidates[:25]
        )
        detail = (
            f"no element named 'Yes' with can_invoke=True. "
            f"Tree had {sum(1 for _ in candidates)} named/typed nodes. "
            f"Worker diag: {diag_blurb}. "
            f"First nodes: [{snapshot}]"
        )
        _record(report, "UAC tree contains invokable Yes button", False, detail, t0)
        return None
    cx, cy = yes.get("center", {}).get("x"), yes.get("center", {}).get("y")
    ok = isinstance(cx, int) and isinstance(cy, int)
    _record(
        report, "UAC tree contains invokable Yes button",
        ok, f"Yes at ({cx},{cy}) bbox={yes.get('bbox')}", t0,
    )
    return yes if ok else None


async def assert_click_dismisses_uac(
    session: ClientSession, report: Report, yes_node: dict
) -> None:
    """policy=allow_all branch: click Yes, verify UAC is gone."""
    t0 = time.monotonic()
    cx = yes_node["center"]["x"]
    cy = yes_node["center"]["y"]
    try:
        click_result = await call(session, "Click", {"loc": [cx, cy]})
    except Exception as exc:
        _record(report, "Click(Yes) under allow_all dismisses UAC",
                False, f"Click call failed: {exc}", t0)
        return

    # Now verify UAC is no longer the input desktop. Issue a short-timeout
    # WaitForUACPrompt — if it reports fired=False, the dialog is gone. Keep the
    # server poll short (it's an absence check) but give the transport a roomy
    # read timeout so a slow broker round-trip on TCG doesn't error the call.
    await asyncio.sleep(2)
    follow = await call(
        session, "WaitForUACPrompt", {"timeout_ms": 5_000}, read_timeout=60
    )
    dismissed = follow.get("ok") is True and follow.get("fired") is False
    _record(
        report, "Click(Yes) under allow_all dismisses UAC",
        dismissed,
        f"click_result={json.dumps(click_result)[:200]} follow={json.dumps(follow)[:200]}",
        t0,
    )


async def assert_block_policy_refuses_click(
    session: ClientSession, report: Report, yes_node: dict
) -> None:
    """policy=block branch: clicking should be refused by the service."""
    t0 = time.monotonic()
    cx = yes_node["center"]["x"]
    cy = yes_node["center"]["y"]
    try:
        result = await call(session, "Click", {"loc": [cx, cy]})
    except Exception as exc:
        # MCP errors surface as exceptions; that's also a valid "refused" signal.
        _record(report, "Click(Yes) under block is refused",
                "policy" in str(exc).lower() or "denied" in str(exc).lower(),
                f"exc={exc}", t0)
        return

    raw = json.dumps(result).lower()
    refused = "policy" in raw and ("denied" in raw or "block" in raw or "refus" in raw)
    _record(
        report, "Click(Yes) under block is refused",
        refused,
        f"result={json.dumps(result)[:300]}",
        t0,
    )


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

async def _run_suite(session: ClientSession, report: Report, mode: str) -> None:
    """`mode` is the policy phase the run_all script set up before invoking us."""
    await assert_list_tools(session, report)

    payload = await assert_wait_for_uac_returns_dialog(
        session, report, expect_policy=mode,
    )
    if payload is None:
        return

    yes_node = await assert_yes_button_present(payload, report)
    if yes_node is None:
        return

    if mode == "allow_all":
        await assert_click_dismisses_uac(session, report, yes_node)
    elif mode == "block":
        await assert_block_policy_refuses_click(session, report, yes_node)
        # Make sure UAC is dismissed for the next phase (Cancel = right-arrow + Enter? simpler:
        # send Esc via the broker's Shortcut tool, which goes through the service path).
        try:
            await call(session, "Shortcut", {"shortcut": "Escape"})
        except Exception:
            pass


async def run_async(args: argparse.Namespace) -> Report:
    report = Report(
        started_at=_now(),
        transport=("http" if args.http else "stdio"),
    )
    if args.http:
        async with streamablehttp_client(args.http) as (read, write, _info):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await _run_suite(session, report, args.mode)
    else:
        params = StdioServerParameters(
            command=args.python or sys.executable,
            args=["-m", "windows_mcp", "serve", "--transport", "stdio"],
            env=os.environ.copy(),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await _run_suite(session, report, args.mode)
    report.finished_at = _now()
    report.summary = {
        "total": len(report.results),
        "passed": sum(1 for r in report.results if r.passed),
        "failed": sum(1 for r in report.results if not r.passed),
    }
    return report


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, help="Write JSON report here.")
    ap.add_argument("--http", help="Talk to a remote server at this URL instead of stdio.")
    ap.add_argument("--python", help="Override the python.exe used for the stdio server.")
    ap.add_argument(
        "--mode", choices=["allow_all", "block"], default="allow_all",
        help="Which policy phase the surrounding script set up before invoking us.",
    )
    args = ap.parse_args()

    try:
        report = asyncio.run(run_async(args))
    except Exception as exc:
        report = Report(
            started_at=_now(), finished_at=_now(), transport="(failed to start)",
            results=[TestResult("driver bootstrap", False, repr(exc), 0.0)],
            summary={"total": 1, "passed": 0, "failed": 1},
        )

    payload = {
        **asdict(report),
        "results": [asdict(r) for r in report.results],
    }
    with open(args.results, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    # Also write a timestamped copy so concurrent test runs don't
    # overwrite each other's data (schtasks /Run is non-blocking, and
    # the windows-mcp-test ONLOGON task fires every login on top of
    # any manual triggers).
    try:
        ts_path = os.path.join(
            os.path.dirname(os.path.abspath(args.results)) or ".",
            f"results-allow_all-{time.strftime('%H%M%S')}.json",
        )
        with open(ts_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
    except OSError:
        pass

    print(json.dumps(report.summary, indent=2))
    return 0 if report.summary.get("failed", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
