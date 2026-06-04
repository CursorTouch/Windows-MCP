"""Worker executed inside the active console user's session.

The LocalSystem host service spawns this helper via ``CreateProcessAsUser``
when it needs to walk or click the UAC consent dialog. Session 0
isolation prevents a service thread from enumerating windows owned by
user-session processes such as ``consent.exe``. A process *inside* the
user's session is not subject to that boundary, and with the user's
elevated linked token + ``SetTokenInformation(TokenUIAccess=1)`` it has
enough access to read consent.exe's higher-integrity UIA tree.

Invocation::

    python -m windows_mcp.service.user_session_worker <op> [args...]

The worker emits a single JSON line on stdout describing the result and
exits with code 0 on success / 1 on failure. The parent service reads the
pipe and forwards the payload over the named-pipe protocol.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="windows-mcp user-session UIA worker")
    sub = p.add_subparsers(dest="op", required=True)
    sub.add_parser("tree", help="Walk the input desktop's UIA tree.")
    sub.add_parser("publisher", help="Extract the UAC consent publisher string.")
    sub.add_parser("windows", help="List top-level window titles on the input desktop.")
    iv = sub.add_parser("invoke", help="Invoke the named UIA element.")
    iv.add_argument("name")
    cl = sub.add_parser("click_at", help="Invoke the UIA element at (x, y).")
    cl.add_argument("x", type=int)
    cl.add_argument("y", type=int)
    ty = sub.add_parser("type_at", help="Type text into the UIA element at (x, y).")
    ty.add_argument("x", type=int)
    ty.add_argument("y", type=int)
    ty.add_argument("text")
    dr = sub.add_parser("drag_from_to", help="Drag from (x1, y1) to (x2, y2).")
    dr.add_argument("x1", type=int)
    dr.add_argument("y1", type=int)
    dr.add_argument("x2", type=int)
    dr.add_argument("y2", type=int)
    return p


def _drain_stdin_handoff() -> None:
    """Broker writes a single line to our stdin then closes it. Pre-iter-8
    that line carried a duplicated Winlogon HDESK and/or consent.exe HWND
    for cross-desktop reads. Iter-8 routes UAC to Default so the broker no
    longer passes anything, but the broker still writes an empty newline
    to signal "no handoff" -- read and discard it so the worker doesn't
    block on stdin in some future change."""
    try:
        os.read(0, 128)
    except OSError:
        pass


def main() -> int:
    # Worker diagnostics go to stderr; stdout is reserved for the JSON payload
    # the parent service reads back.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="[user-session-worker pid=%(process)d] %(message)s",
    )
    _drain_stdin_handoff()
    args = _build_parser().parse_args()

    # Import lazily so a failed import surfaces as JSON instead of a Python
    # traceback the parent can't parse.
    try:
        from windows_mcp.service import secure_desktop
    except Exception as exc:
        json.dump(
            {"ok": False, "error": f"import failed: {exc}", "type": type(exc).__name__},
            sys.stdout,
        )
        return 1

    try:
        if args.op == "tree":
            result = secure_desktop.uia_get_tree()
        elif args.op == "publisher":
            result = secure_desktop.get_uac_publisher()
        elif args.op == "windows":
            result = secure_desktop.uia_get_window_titles()
        elif args.op == "invoke":
            result = secure_desktop.uia_invoke_element(args.name)
        elif args.op == "click_at":
            result = secure_desktop.uia_click_at(args.x, args.y)
        elif args.op == "type_at":
            result = secure_desktop.uia_type_at(args.x, args.y, args.text)
        elif args.op == "drag_from_to":
            result = secure_desktop.uia_drag_from_to(args.x1, args.y1, args.x2, args.y2)
        else:
            json.dump({"ok": False, "error": f"unknown op: {args.op}"}, sys.stdout)
            return 1
    except Exception as exc:
        logger.exception("op %s failed", args.op)
        json.dump(
            {"ok": False, "error": str(exc), "type": type(exc).__name__},
            sys.stdout,
        )
        return 1

    json.dump({"ok": True, "result": result}, sys.stdout)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
