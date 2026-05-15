"""Worker executed inside the active console user's session.

The LocalSystem host service spawns this helper via ``CreateProcessAsUser``
when it needs to walk or click the Winlogon (Secure Desktop) UIA tree.

Session 0 isolation prevents a service thread — even one bound to
``WinSta0\\Winlogon`` via ``SetProcessWindowStation`` + ``SetThreadDesktop``
— from enumerating windows owned by user-session processes such as
``consent.exe``. A process *inside* the user's session is not subject to
that boundary, and with the user's elevated linked token it has enough
access to the Winlogon desktop to walk the consent dialog normally.

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


def _log_token_diag() -> None:
    """Log uiAccess + integrity level + current desktop. Drives diagnosis when
    the worker silently enumerates the wrong UIA tree."""
    try:
        import ctypes
        import ctypes.wintypes as wt
        TokenUIAccess = 26
        TokenIntegrityLevel = 25
        h_token = wt.HANDLE()
        ctypes.windll.advapi32.OpenProcessToken(
            ctypes.windll.kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(h_token)
        )
        ui_access = wt.DWORD(0)
        rlen = wt.DWORD(0)
        ctypes.windll.advapi32.GetTokenInformation(
            h_token, TokenUIAccess, ctypes.byref(ui_access), 4, ctypes.byref(rlen)
        )
        # Current desktop name
        buf = ctypes.create_unicode_buffer(256)
        needed = wt.DWORD()
        hdesk = ctypes.windll.user32.GetThreadDesktop(
            ctypes.windll.kernel32.GetCurrentThreadId()
        )
        ctypes.windll.user32.GetUserObjectInformationW(
            hdesk, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed)
        )
        logger.info(
            "diag: TokenUIAccess=%d initial-desktop=%r",
            ui_access.value, buf.value,
        )
    except Exception as exc:
        logger.warning("token diagnostics failed: %s", exc)


def main() -> int:
    # Worker diagnostics go to stderr; stdout is reserved for the JSON payload
    # the parent service reads back.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="[user-session-worker pid=%(process)d] %(message)s",
    )
    _log_token_diag()
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
