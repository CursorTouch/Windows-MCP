"""Privileged desktop primitives — runs inside the LocalSystem host service.

All public functions here are called from the pipe server thread inside the
Windows service.  They must be called from a LocalSystem process; calling them
from a normal user-mode process will silently degrade (OpenInputDesktop returns
NULL for Winlogon, SetThreadDesktop has no effect).

Desktop access sequence
-----------------------
From Session 0 (where Windows services run), the interactive window station
"WinSta0" is not the default.  We must:

  1. OpenWindowStation("WinSta0") → SetProcessWindowStation()
  2. OpenInputDesktop()  — returns a handle to whichever desktop currently
     receives keyboard/mouse input (Default during normal use, Winlogon during
     UAC).
  3. SetThreadDesktop()  — attaches the calling thread to that desktop so that
     GDI/UIA calls resolve against the correct desktop object.

This is the same pattern used by LookingGlass, RustDesk, and Splashtop.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import io
import json
import logging
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------

_UOI_NAME = 2
_WINSTA_ALL_ACCESS = 0x037F
_DESKTOP_ALL_ACCESS = 0x01FF
_DESKTOP_READOBJECTS = 0x0001
_DESKTOP_ENUMERATE = 0x0040
_DESKTOP_SWITCHDESKTOP = 0x0100
# Minimum rights to SetThreadDesktop + walk UIA on the input desktop.
# Winlogon's DACL doesn't grant ALL_ACCESS to admin tokens, so the read-only
# attach is the only one that actually succeeds for the user-session worker
# enumerating consent.exe.
_DESKTOP_READ_ATTACH = _DESKTOP_SWITCHDESKTOP | _DESKTOP_ENUMERATE | _DESKTOP_READOBJECTS

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# Declare argtypes/restype on the user32 desktop/window-station functions
# we call. Without these, ctypes passes Python str as a c_char_p (ASCII
# bytes) but the *W APIs expect LPCWSTR (UTF-16) -- the call sees garbled
# wide characters and returns NULL, so OpenDesktopW('Winlogon', ...) always
# fails for the user-session worker and _input_desktop falls back to
# OpenInputDesktop which (from inside a user session) returns the user's
# Default desktop instead of Winlogon, leaving consent.exe outside the UIA
# enumeration scope.
_user32.OpenWindowStationW.argtypes = [
    ctypes.wintypes.LPCWSTR, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD,
]
_user32.OpenWindowStationW.restype = ctypes.wintypes.HANDLE
_user32.OpenDesktopW.argtypes = [
    ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.BOOL,
    ctypes.wintypes.DWORD,
]
_user32.OpenDesktopW.restype = ctypes.wintypes.HANDLE
_user32.OpenInputDesktop.argtypes = [
    ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD,
]
_user32.OpenInputDesktop.restype = ctypes.wintypes.HANDLE
_user32.SetThreadDesktop.argtypes = [ctypes.wintypes.HANDLE]
_user32.SetThreadDesktop.restype = ctypes.wintypes.BOOL
_user32.SetProcessWindowStation.argtypes = [ctypes.wintypes.HANDLE]
_user32.SetProcessWindowStation.restype = ctypes.wintypes.BOOL
_user32.GetProcessWindowStation.restype = ctypes.wintypes.HANDLE
_user32.GetThreadDesktop.argtypes = [ctypes.wintypes.DWORD]
_user32.GetThreadDesktop.restype = ctypes.wintypes.HANDLE
_user32.CloseDesktop.argtypes = [ctypes.wintypes.HANDLE]
_user32.CloseDesktop.restype = ctypes.wintypes.BOOL
_user32.CloseWindowStation.argtypes = [ctypes.wintypes.HANDLE]
_user32.CloseWindowStation.restype = ctypes.wintypes.BOOL
_user32.GetUserObjectInformationW.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
    ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.DWORD),
]
_user32.GetUserObjectInformationW.restype = ctypes.wintypes.BOOL
_kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD

# When set (by the user-session worker after reading from broker-passed
# stdin), _input_desktop() attaches the thread to this handle instead of
# trying to OpenDesktopW("Winlogon") itself — which fails for non-SYSTEM
# tokens. See _spawn_in_user_session for the broker side.
_preattached_winlogon_hdesk: int = 0

# UIA constants
_UIA_InvokePatternId = 10000
_UIA_NamePropertyId = 30005
_UIA_TreeScope_Descendants = 4


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _open_winsta0() -> int:
    handle = _user32.OpenWindowStationW("WinSta0", False, _WINSTA_ALL_ACCESS)
    return handle or 0


def _open_input_desktop(access: int = _DESKTOP_ALL_ACCESS) -> int:
    handle = _user32.OpenInputDesktop(0, False, access)
    return handle or 0


def _open_desktop_by_name(name: str, access: int = _DESKTOP_ALL_ACCESS) -> int:
    handle = _user32.OpenDesktopW(name, 0, False, access)
    if not handle:
        gle = ctypes.GetLastError()
        logger.debug(
            "OpenDesktopW(%r, access=0x%04x) failed (gle=%d)",
            name, access, gle,
        )
    return handle or 0


def _get_desktop_name(hdesk: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    needed = ctypes.wintypes.DWORD()
    _user32.GetUserObjectInformationW(
        hdesk, _UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)
    )
    return buf.value


@contextmanager
def _input_desktop(prefer_winlogon: bool = True):
    """Switch process/thread to the input desktop, then restore on exit.

    From a user-session process, ``OpenInputDesktop`` returns the user's
    *Default* desktop even while UAC is up — Secure Desktop is intentionally
    isolated from the user session, and the user-session worker can't see
    Winlogon as the input desktop. So when *prefer_winlogon* is true (the
    case for UAC tree walking), we first try opening "Winlogon" by name and
    only fall back to OpenInputDesktop if that fails. The handle-by-name
    path goes through ``OpenDesktopW`` which Winlogon's DACL does grant to
    admin tokens (and to uiAccess processes).

    Tries ALL_ACCESS first (needed for synthetic input ops), falls back to
    the minimum read+switch mask. Logs which path actually stuck (and a
    loud warning if nothing did) so future regressions don't fail silently.
    """
    hwinsta_prev = _user32.GetProcessWindowStation()
    hdesk_prev = _user32.GetThreadDesktop(_kernel32.GetCurrentThreadId())
    hwinsta = _open_winsta0()
    if hwinsta:
        _user32.SetProcessWindowStation(hwinsta)
    hdesk = 0
    own_hdesk = True  # whether we should close this handle on exit
    attached_via = None
    attached_how = None
    # If the broker handed us a Winlogon desktop handle via stdin, use it
    # directly. The handle belongs to the worker process for its lifetime —
    # don't close it in finally.
    if prefer_winlogon and _preattached_winlogon_hdesk:
        if _user32.SetThreadDesktop(_preattached_winlogon_hdesk):
            hdesk = _preattached_winlogon_hdesk
            own_hdesk = False
            attached_how = "broker-handoff"
            attached_via = _DESKTOP_ALL_ACCESS
    if not hdesk:
        candidates: list[tuple[str, Any]] = []
        if prefer_winlogon:
            candidates.append(("Winlogon-by-name",
                               lambda access: _open_desktop_by_name("Winlogon", access)))
        candidates.append(("input-desktop", _open_input_desktop))
        for how, opener in candidates:
            for access in (_DESKTOP_ALL_ACCESS, _DESKTOP_READ_ATTACH):
                hdesk = opener(access)
                if not hdesk:
                    continue
                if _user32.SetThreadDesktop(hdesk):
                    attached_via = access
                    attached_how = how
                    break
                # SetThreadDesktop failed even though we got a handle — drop
                # it and try a narrower access mask.
                _user32.CloseDesktop(hdesk)
                hdesk = 0
            if hdesk:
                break
    if hdesk:
        name = _get_desktop_name(hdesk) or "(unknown)"
        logger.info(
            "_input_desktop: attached to %r via %s access=0x%04x",
            name, attached_how, attached_via,
        )
    else:
        logger.warning(
            "_input_desktop: could not attach to any candidate desktop "
            "(prefer_winlogon=%s). The thread will stay on the worker's "
            "initial desktop and UIA enumeration will reflect that desktop.",
            prefer_winlogon,
        )
    try:
        yield
    finally:
        if hdesk:
            _user32.SetThreadDesktop(hdesk_prev)
            if own_hdesk:
                _user32.CloseDesktop(hdesk)
        if hwinsta:
            _user32.SetProcessWindowStation(hwinsta_prev)
            _user32.CloseWindowStation(hwinsta)


def _run_on_fresh_thread(fn, timeout: float = 15.0) -> Any:
    """Run *fn* on a brand-new thread and return its result (or re-raise its exception).

    IUIAutomation must be created AFTER SetThreadDesktop is called, and COM
    must be initialized AFTER IUIAutomation is created — so both steps must
    happen on the same thread in the right order.  The long-lived pipe-server
    thread has its COM apartment already set up (for the wrong desktop), so
    creating IUIAutomation there silently binds it to Session 0's default
    desktop instead of the Winlogon desktop.

    Spawning a fresh thread guarantees:
      1. No prior COM initialization on this thread.
      2. _input_desktop() calls SetThreadDesktop before anything else.
      3. CoInitialize runs in the correct desktop context.
      4. CoUninitialize cleans up on thread exit.
    """
    result: list[Any] = []
    exc: list[BaseException] = []

    def _wrapper():
        try:
            result.append(fn())
        except Exception as e:
            exc.append(e)

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if exc:
        raise exc[0]
    return result[0] if result else None


def _create_uia() -> tuple[Any, Any]:
    """Return (IUIAutomation, uia_core) — must be called on a thread with no prior COM init."""
    import comtypes.client
    ctypes.windll.ole32.CoInitialize(None)  # STA — matches the existing user-mode UIA code
    uia_core = comtypes.client.GetModule("UIAutomationCore.dll")
    iuia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}",
        interface=uia_core.IUIAutomation,
    )
    return iuia, uia_core


def _serialize_element(element: Any, walker: Any, depth: int = 0) -> dict | None:
    """Recursively serialize a UIA element to a JSON-safe dict."""
    if depth > 8:
        return None
    try:
        rect = element.CurrentBoundingRectangle
        name = element.CurrentName or ""
        ctrl = element.CurrentLocalizedControlType or ""

        can_invoke = False
        try:
            can_invoke = element.GetCurrentPattern(_UIA_InvokePatternId) is not None
        except Exception:
            pass

        children: list[dict] = []
        try:
            child = walker.GetFirstChildElement(element)
            while child:
                node = _serialize_element(child, walker, depth + 1)
                if node:
                    children.append(node)
                child = walker.GetNextSiblingElement(child)
        except Exception:
            pass

        w = rect.right - rect.left
        h = rect.bottom - rect.top
        return {
            "name": name,
            "control_type": ctrl,
            "bbox": {
                "left": rect.left, "top": rect.top,
                "right": rect.right, "bottom": rect.bottom,
                "width": w, "height": h,
            },
            "center": {"x": rect.left + w // 2, "y": rect.top + h // 2},
            "can_invoke": can_invoke,
            "children": children,
        }
    except Exception as exc:
        logger.debug("_serialize_element error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_input_desktop_name() -> str:
    """Return the name of the current input desktop.

    Returns ``"Default"`` during normal desktop use and ``"Winlogon"`` while a
    UAC prompt is active.  Works from user-mode too (used for detection in the
    broker via :func:`~windows_mcp.desktop.screenshot.is_secure_desktop_active`).

    When called from a LocalSystem service the process window station is
    ``Service-0x0-3e7$``, not ``WinSta0`` — and ``OpenInputDesktop`` on the
    service winstation never returns the user's input desktop. We first try a
    plain ``OpenInputDesktop`` (cheap, works from user mode) and fall back to
    momentarily attaching to ``WinSta0`` when that returns nothing useful.
    """
    hdesk = _open_input_desktop(_DESKTOP_READOBJECTS)
    if hdesk:
        try:
            name = _get_desktop_name(hdesk)
        finally:
            _user32.CloseDesktop(hdesk)
        if name:
            return name

    hwinsta_prev = _user32.GetProcessWindowStation()
    hwinsta = _open_winsta0()
    if not hwinsta:
        return "Default"
    try:
        _user32.SetProcessWindowStation(hwinsta)
        hdesk = _open_input_desktop(_DESKTOP_READOBJECTS)
        if not hdesk:
            return "Default"
        try:
            return _get_desktop_name(hdesk) or "Default"
        finally:
            _user32.CloseDesktop(hdesk)
    finally:
        _user32.SetProcessWindowStation(hwinsta_prev)
        _user32.CloseWindowStation(hwinsta)


def capture_screenshot() -> bytes:
    """Capture the current input desktop as PNG bytes.

    Uses GDI (Pillow ImageGrab) after SetThreadDesktop — DXGI is unavailable
    from Session 0, but GDI BitBlt works once the thread is on the right desktop.
    """
    with _input_desktop():
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def uia_get_window_titles() -> list[str]:
    """Return names of top-level windows on the current input desktop."""
    def _work() -> list[str]:
        titles: list[str] = []
        with _input_desktop():
            iuia, _ = _create_uia()
            root = iuia.GetRootElement()
            walker = iuia.RawViewWalker
            child = walker.GetFirstChildElement(root)
            while child:
                try:
                    name = child.CurrentName
                    if name:
                        titles.append(name)
                except Exception:
                    pass
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:
                    break
            return titles

    try:
        return _run_on_fresh_thread(_work) or []
    except Exception as exc:
        logger.warning("uia_get_window_titles failed: %s", exc)
        return []


def uia_get_tree() -> list[dict]:
    """Return the full UIA tree of the current input desktop.

    Each entry is a top-level window serialized as a nested dict.  Elements with
    ``can_invoke=True`` support ``IUIAutomationInvokePattern`` — the broker uses
    this to identify clickable buttons (Yes/No on a UAC dialog) without
    re-walking the tree.

    Runs on a fresh thread so COM initialises *after* SetThreadDesktop, binding
    IUIAutomation to the correct desktop (Winlogon during UAC).
    """
    def _work() -> list[dict]:
        nodes: list[dict] = []
        with _input_desktop():
            iuia, _ = _create_uia()
            root = iuia.GetRootElement()
            walker = iuia.RawViewWalker
            child = walker.GetFirstChildElement(root)
            while child:
                node = _serialize_element(child, walker)
                if node:
                    nodes.append(node)
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:
                    break
        return nodes

    try:
        return _run_on_fresh_thread(_work) or []
    except Exception as exc:
        logger.error("uia_get_tree failed: %s", exc)
        return []


def uia_invoke_element(name: str) -> bool:
    """Find a named element on the input desktop and invoke it via UIA.

    Uses ``IUIAutomation.FindFirst`` + ``IUIAutomationInvokePattern.Invoke()``.
    Direct COM call — no input injection needed, works from Session 0.
    Runs on a fresh thread so COM binds to the Winlogon desktop.
    """
    def _work() -> bool:
        with _input_desktop():
            iuia, uia_core = _create_uia()
            root = iuia.GetRootElement()
            condition = iuia.CreatePropertyCondition(_UIA_NamePropertyId, name)
            element = root.FindFirst(_UIA_TreeScope_Descendants, condition)
            if element is None:
                logger.warning("uia_invoke_element: no element named %r", name)
                return False
            pattern = element.GetCurrentPattern(_UIA_InvokePatternId)
            if pattern is None:
                logger.warning("uia_invoke_element: %r has no InvokePattern", name)
                return False
            invoke = pattern.QueryInterface(uia_core.IUIAutomationInvokePattern)
            invoke.Invoke()
            logger.info("uia_invoke_element: invoked %r", name)
            return True

    try:
        return _run_on_fresh_thread(_work) or False
    except Exception as exc:
        logger.error("uia_invoke_element(%r) failed: %s", name, exc)
        return False


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def uia_click_at(x: int, y: int) -> bool:
    """Invoke the element at (x, y) on the input desktop via UIA ElementFromPoint.

    Callers can pass coordinates straight from the screenshot.  Runs on a fresh
    thread so COM binds to the correct (Winlogon) desktop.
    """
    def _work() -> bool:
        with _input_desktop():
            iuia, uia_core = _create_uia()
            element = iuia.ElementFromPoint(_POINT(x, y))
            if element is None:
                logger.warning("uia_click_at(%d,%d): no element found", x, y)
                return False
            pattern = element.GetCurrentPattern(_UIA_InvokePatternId)
            if pattern is None:
                logger.warning("uia_click_at(%d,%d): no InvokePattern", x, y)
                return False
            invoke = pattern.QueryInterface(uia_core.IUIAutomationInvokePattern)
            invoke.Invoke()
            logger.info("uia_click_at(%d,%d): invoked %r", x, y, element.CurrentName)
            return True

    try:
        return _run_on_fresh_thread(_work) or False
    except Exception as exc:
        logger.error("uia_click_at(%d,%d) failed: %s", x, y, exc)
        return False


# Additional UIA constants (for ValuePattern, used by Type)
_UIA_ValuePatternId = 10002


def uia_type_at(x: int, y: int, text: str) -> bool:
    """Set the value of the editable element at (x, y) on the input desktop.

    Uses the IUIAutomationValuePattern.SetValue method — works from Session 0
    without any input injection, so it crosses the Winlogon boundary safely.
    """
    def _work() -> bool:
        with _input_desktop():
            iuia, uia_core = _create_uia()
            element = iuia.ElementFromPoint(_POINT(x, y))
            if element is None:
                logger.warning("uia_type_at(%d,%d): no element found", x, y)
                return False
            pattern = element.GetCurrentPattern(_UIA_ValuePatternId)
            if pattern is None:
                logger.warning("uia_type_at(%d,%d): no ValuePattern", x, y)
                return False
            value = pattern.QueryInterface(uia_core.IUIAutomationValuePattern)
            value.SetValue(text)
            logger.info("uia_type_at(%d,%d): set value on %r", x, y, element.CurrentName)
            return True

    try:
        return _run_on_fresh_thread(_work) or False
    except Exception as exc:
        logger.error("uia_type_at(%d,%d) failed: %s", x, y, exc)
        return False


def uia_drag_from_to(x1: int, y1: int, x2: int, y2: int) -> bool:
    """Drag the element at (x1, y1) onto (x2, y2) using UIA DragPattern when present.

    Cross-desktop drag with native Win32 input is unreliable because mouse_event
    cannot be retargeted across Session 0's desktop boundary.  This implementation
    relies on the source element supporting the legacy IAccessible "DoDefaultAction"
    drag or a UIA Transform/Move pattern; it is best-effort and intentionally
    narrower than the in-process drag the broker performs on the Default desktop.
    Most UAC consent dialogs do not need drag, so this is here for completeness.
    """
    _UIA_TransformPatternId = 10016
    def _work() -> bool:
        with _input_desktop():
            iuia, uia_core = _create_uia()
            src = iuia.ElementFromPoint(_POINT(x1, y1))
            if src is None:
                return False
            try:
                pattern = src.GetCurrentPattern(_UIA_TransformPatternId)
                if pattern is None:
                    return False
                transform = pattern.QueryInterface(uia_core.IUIAutomationTransformPattern)
                transform.Move(x2, y2)
                logger.info("uia_drag_from_to: moved %r to (%d,%d)", src.CurrentName, x2, y2)
                return True
            except Exception:
                return False

    try:
        return _run_on_fresh_thread(_work) or False
    except Exception as exc:
        logger.error("uia_drag_from_to(%d,%d->%d,%d) failed: %s", x1, y1, x2, y2, exc)
        return False


# ---------------------------------------------------------------------------
# UAC dialog inspection
# ---------------------------------------------------------------------------

# Patterns the Windows UAC dialog uses for its "verified publisher" line.
# These are localised on non-English Windows; if no pattern matches we return None
# and the allow_with_match policy refuses on caller side.
_PUBLISHER_PATTERNS = [
    re.compile(r"Verified publisher:\s*(.+)", re.IGNORECASE),
    re.compile(r"Program name:\s*(.+)", re.IGNORECASE),
    re.compile(r"Publisher:\s*(.+)", re.IGNORECASE),
]


def get_uac_publisher() -> str | None:
    """Inspect the active UAC consent dialog and return its publisher string.

    Returns ``None`` if no UAC dialog is currently displayed, if its layout does
    not match the expected English pattern, or if reading the UIA tree fails.
    """
    def _work() -> str | None:
        with _input_desktop():
            iuia, _ = _create_uia()
            root = iuia.GetRootElement()
            walker = iuia.RawViewWalker
            collected: list[str] = []

            def _collect(elem: Any, depth: int = 0) -> None:
                if depth > 8:
                    return
                try:
                    name = elem.CurrentName or ""
                    if name:
                        collected.append(name)
                except Exception:
                    return
                try:
                    child = walker.GetFirstChildElement(elem)
                    while child:
                        _collect(child, depth + 1)
                        try:
                            child = walker.GetNextSiblingElement(child)
                        except Exception:
                            break
                except Exception:
                    pass

            child = walker.GetFirstChildElement(root)
            while child:
                _collect(child)
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:
                    break

            text = "\n".join(collected)
            for pat in _PUBLISHER_PATTERNS:
                match = pat.search(text)
                if match:
                    return match.group(1).strip()
            return None

    try:
        return _run_on_fresh_thread(_work)
    except Exception as exc:
        logger.warning("get_uac_publisher failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# User-session worker spawn
# ---------------------------------------------------------------------------


def _spawn_in_user_session(*op_args: str, timeout: float = 30.0) -> Any:
    """Run one ``user_session_worker`` op inside the active console user's session.

    Session 0 isolation blocks the LocalSystem service from walking UIA trees
    owned by user-session processes (consent.exe is the case that matters for
    UAC). We side-step that by ``CreateProcessAsUser``-ing a one-shot helper
    into the interactive session — UIA from inside the user's session sees
    Winlogon normally — and parse the JSON it writes to stdout.

    Uses the user's *linked* elevated token when available so the helper has
    enough access to enumerate consent.exe; falls back to the standard user
    token otherwise.
    """
    import pywintypes
    import win32api
    import win32con
    import win32event
    import win32file
    import win32pipe
    import win32process
    import win32profile  # CreateEnvironmentBlock lives here, not in win32process
    import win32security
    import win32ts

    session_id = win32ts.WTSGetActiveConsoleSessionId()
    if session_id in (0xFFFFFFFF, 0):
        raise RuntimeError(
            "no interactive console session is active "
            "(WTSGetActiveConsoleSessionId returned no user session)"
        )

    user_token = win32ts.WTSQueryUserToken(session_id)
    elevated_token = None
    try:
        elevated_token = win32security.GetTokenInformation(
            user_token, win32security.TokenLinkedToken
        )
    except Exception:
        elevated_token = None
    spawn_token = elevated_token or user_token
    using_elevated = bool(elevated_token)

    # Enable SeTcbPrivilege on the broker's process token. SYSTEM has it,
    # but it isn't enabled by default. SetTokenInformation(TokenUIAccess)
    # requires this privilege to be ENABLED on the caller, not just held.
    try:
        TOKEN_ADJUST_PRIVILEGES = 0x0020
        TOKEN_QUERY = 0x0008
        SE_PRIVILEGE_ENABLED = 0x00000002

        # Declare argtypes so ctypes doesn't truncate the GetCurrentProcess
        # pseudo-handle (-1) into ERROR_INVALID_HANDLE on 64-bit.
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32
        kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = [
            ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
            ctypes.POINTER(ctypes.wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = ctypes.wintypes.BOOL
        advapi32.LookupPrivilegeValueW.argtypes = [
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_uint64),
        ]
        advapi32.LookupPrivilegeValueW.restype = ctypes.wintypes.BOOL
        advapi32.AdjustTokenPrivileges.argtypes = [
            ctypes.wintypes.HANDLE, ctypes.wintypes.BOOL,
            ctypes.c_void_p, ctypes.wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p,
        ]
        advapi32.AdjustTokenPrivileges.restype = ctypes.wintypes.BOOL

        h_proc_token = ctypes.wintypes.HANDLE()
        ok = advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            ctypes.byref(h_proc_token),
        )
        if ok:
            luid = ctypes.c_uint64(0)
            if advapi32.LookupPrivilegeValueW(
                None, "SeTcbPrivilege", ctypes.byref(luid)
            ):
                # TOKEN_PRIVILEGES { DWORD count; LUID_AND_ATTRIBUTES privs[1]; }
                # LUID_AND_ATTRIBUTES { LUID(8 bytes); DWORD attrs; }
                tp_buf = (ctypes.c_uint32 * 4)()
                tp_buf[0] = 1                          # PrivilegeCount
                tp_buf[1] = luid.value & 0xFFFFFFFF    # LUID.LowPart
                tp_buf[2] = (luid.value >> 32) & 0xFFFFFFFF  # LUID.HighPart
                tp_buf[3] = SE_PRIVILEGE_ENABLED       # Attributes
                ok2 = advapi32.AdjustTokenPrivileges(
                    h_proc_token, False, ctypes.byref(tp_buf), 0, None, None
                )
                gle = ctypes.GetLastError() if not ok2 else 0
                logger.info(
                    "AdjustTokenPrivileges(SeTcbPrivilege=ENABLED) ok=%s gle=%d",
                    bool(ok2), gle,
                )
            else:
                logger.warning("LookupPrivilegeValueW(SeTcbPrivilege) failed gle=%d",
                               ctypes.GetLastError())
            kernel32.CloseHandle(h_proc_token)
        else:
            logger.warning("OpenProcessToken failed gle=%d", ctypes.GetLastError())
    except Exception as exc:
        logger.warning("SeTcbPrivilege enable raised: %s", exc)

    # Set TokenUIAccess=1 on the token *before* CreateProcessAsUser. Without
    # this the spawned process always boots with TokenUIAccess=0 — Windows
    # only checks the manifest's uiAccess attribute as a *request*, the
    # privilege itself comes from this flag on the primary token, and
    # CreateProcessAsUser does not set it for us based on the exe's manifest.
    # SetTokenInformation(TokenUIAccess) from a SYSTEM caller with
    # SeTcbPrivilege enabled bypasses the signature + trusted-path checks
    # AppInfo normally enforces; see https://learn.microsoft.com/en-us/answers/questions/1009084/
    # and Tyranid's notes at https://www.tiraniddo.dev/2019/02/
    try:
        TOKEN_UI_ACCESS = 26
        # Declare argtypes -- without them, ctypes treats the HANDLE arg as
        # c_int (4 bytes) and truncates the high 32 bits of the 8-byte
        # PyHANDLE. The call then operates on a corrupted handle (which on
        # this box happened to "succeed" -- gle=0, ok=1 -- presumably because
        # the truncated value collided with some other open handle), so the
        # real spawn token never gets its UIAccess bit set and the spawned
        # worker boots with TokenUIAccess=0.
        advapi32.SetTokenInformation.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.c_int,           # TOKEN_INFORMATION_CLASS enum
            ctypes.c_void_p,        # LPVOID TokenInformation
            ctypes.wintypes.DWORD,  # TokenInformationLength
        ]
        advapi32.SetTokenInformation.restype = ctypes.wintypes.BOOL

        ui_access = ctypes.c_uint32(1)
        ok = advapi32.SetTokenInformation(
            int(spawn_token),
            TOKEN_UI_ACCESS,
            ctypes.cast(ctypes.byref(ui_access), ctypes.c_void_p),
            ctypes.sizeof(ui_access),
        )
        if not ok:
            gle = ctypes.GetLastError()
            logger.warning(
                "SetTokenInformation(TokenUIAccess=1) failed (gle=%d) - "
                "worker will spawn without UIAccess and won't be able to "
                "walk Winlogon",
                gle,
            )
        else:
            logger.info(
                "SetTokenInformation(TokenUIAccess=1) on spawn token "
                "(handle=0x%x) OK", int(spawn_token),
            )
    except Exception as exc:
        logger.warning("SetTokenInformation(TokenUIAccess) raised: %s", exc)

    sa = win32security.SECURITY_ATTRIBUTES()
    sa.bInheritHandle = True
    stdout_r, stdout_w = win32pipe.CreatePipe(sa, 0)
    stderr_r, stderr_w = win32pipe.CreatePipe(sa, 0)
    stdin_r, stdin_w = win32pipe.CreatePipe(sa, 0)
    # Read ends stay in the service; do not let them leak into the child.
    win32api.SetHandleInformation(stdout_r, win32con.HANDLE_FLAG_INHERIT, 0)
    win32api.SetHandleInformation(stderr_r, win32con.HANDLE_FLAG_INHERIT, 0)
    # Worker reads stdin; the broker's write end must stay non-inheritable.
    win32api.SetHandleInformation(stdin_w, win32con.HANDLE_FLAG_INHERIT, 0)

    # The user-session worker cannot OpenDesktopW("Winlogon") itself even
    # with UIAccess + admin token (Winlogon's DACL denies non-SYSTEM). The
    # broker is SYSTEM and *does* have access, so for read ops that need
    # to walk consent.exe (tree, publisher) we open Winlogon here and
    # duplicate the handle into the spawned worker. The worker reads the
    # duplicated value from stdin and SetThreadDesktop's onto it directly.
    hdesk_winlogon = 0
    pass_winlogon = bool(op_args) and op_args[0] in ("tree", "publisher")
    if pass_winlogon:
        hdesk_winlogon = _open_desktop_by_name("Winlogon", _DESKTOP_ALL_ACCESS)
        if not hdesk_winlogon:
            logger.warning(
                "broker could not open Winlogon — worker will fall back "
                "to its own enumeration (likely returns wrong desktop)"
            )

    # Prefer a UIAccess-signed worker installed in a trusted path. Without
    # it, the unsigned fallback (this Python module) cannot enumerate
    # consent.exe's UIA tree across the integrity boundary — see
    # docs/secure-desktop.md and policy.read_uia_worker_path().
    from windows_mcp.service import policy as _policy_mod
    signed_worker = _policy_mod.read_uia_worker_path()
    if signed_worker:
        argv = [signed_worker, *op_args]
    else:
        argv = [
            sys.executable,
            "-m",
            "windows_mcp.service.user_session_worker",
            *op_args,
        ]
    cmd_line = subprocess.list2cmdline(argv)

    startup = win32process.STARTUPINFO()
    startup.dwFlags = win32con.STARTF_USESTDHANDLES
    startup.hStdInput = stdin_r
    startup.hStdOutput = stdout_w
    startup.hStdError = stderr_w
    # Spawn on the interactive Default desktop; the worker re-binds its own
    # thread to whichever desktop it needs via _input_desktop() before
    # touching UIA. (Specifying lpDesktop="winsta0\winlogon" here trips
    # STATUS_DLL_INIT_FAILED because the user token has no rights on the
    # Winlogon desktop — verified against the host log.)
    startup.lpDesktop = r"winsta0\default"

    user_env = win32profile.CreateEnvironmentBlock(spawn_token, False)

    creation_flags = (
        win32con.CREATE_NO_WINDOW
        | win32process.CREATE_UNICODE_ENVIRONMENT
    )

    proc_handle = thread_handle = None
    try:
        proc_info = win32process.CreateProcessAsUser(
            spawn_token,
            None,
            cmd_line,
            None,
            None,
            True,
            creation_flags,
            user_env,
            None,
            startup,
        )
        proc_handle, thread_handle, _pid, _tid = proc_info
    finally:
        # Now that the child has inherited the write ends we can drop ours.
        try: win32file.CloseHandle(stdout_w)
        except Exception: pass
        try: win32file.CloseHandle(stderr_w)
        except Exception: pass
        try: win32file.CloseHandle(stdin_r)
        except Exception: pass
        try: win32api.CloseHandle(user_token)
        except Exception: pass
        if elevated_token:
            try: win32api.CloseHandle(elevated_token)
            except Exception: pass

    # Hand the worker a Winlogon desktop handle it can't open itself.
    # DuplicateHandle into the child with the same access mask we used,
    # then write the duplicated value to its stdin so it can SetThreadDesktop
    # before any UIA call. Worker reads exactly one line of stdin at boot.
    handoff_line = "\n"  # default: no handoff
    if hdesk_winlogon and proc_handle:
        try:
            dup = win32api.DuplicateHandle(
                win32api.GetCurrentProcess(),
                hdesk_winlogon,
                proc_handle,
                0,            # ignored under DUPLICATE_SAME_ACCESS
                False,        # bInheritHandle
                2,            # DUPLICATE_SAME_ACCESS
            )
            handoff_line = f"WINLOGON_HDESK={int(dup)}\n"
        except Exception as exc:
            logger.warning("Winlogon DuplicateHandle into worker failed: %s", exc)
    try:
        win32file.WriteFile(stdin_w, handoff_line.encode("utf-8"))
    except Exception as exc:
        logger.warning("writing winlogon handoff to worker stdin failed: %s", exc)
    finally:
        try: win32file.CloseHandle(stdin_w)
        except Exception: pass
        if hdesk_winlogon:
            try: _user32.CloseDesktop(hdesk_winlogon)
            except Exception: pass

    logger.info(
        "spawned user-session worker pid=? session=%d elevated=%s op=%s "
        "winlogon_handoff=%s",
        session_id, using_elevated, " ".join(op_args),
        handoff_line.strip() or "<none>",
    )

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def _drain(handle: Any, sink: list[bytes]) -> None:
        while True:
            try:
                _, chunk = win32file.ReadFile(handle, 4096)
            except pywintypes.error as exc:
                if exc.winerror in (109, 233):  # BROKEN_PIPE, NO_DATA
                    return
                raise
            if not chunk:
                return
            sink.append(bytes(chunk))

    import threading as _threading
    err_thread = _threading.Thread(target=_drain, args=(stderr_r, stderr_chunks), daemon=True)
    err_thread.start()
    try:
        _drain(stdout_r, stdout_chunks)
    finally:
        err_thread.join(timeout=1.0)

    try:
        win32event.WaitForSingleObject(proc_handle, int(timeout * 1000))
        exit_code = win32process.GetExitCodeProcess(proc_handle)
    finally:
        try: win32file.CloseHandle(stdout_r)
        except Exception: pass
        try: win32file.CloseHandle(stderr_r)
        except Exception: pass
        try: win32api.CloseHandle(proc_handle)
        except Exception: pass
        try: win32api.CloseHandle(thread_handle)
        except Exception: pass

    stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace").strip()
    stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
    if stderr_text:
        logger.info("user-session worker stderr: %s", stderr_text)

    if not stdout_text:
        raise RuntimeError(
            f"user-session worker produced no stdout "
            f"(exit={exit_code}, stderr={stderr_text!r})"
        )
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"user-session worker stdout not JSON (exit={exit_code}): {stdout_text!r}"
        ) from exc
    if not payload.get("ok"):
        raise RuntimeError(
            f"user-session worker error: {payload.get('error', 'unknown')}"
        )
    return payload.get("result")


# ---------------------------------------------------------------------------
# WaitForUACPrompt
# ---------------------------------------------------------------------------


def wait_for_uac_prompt(timeout_ms: int = 60_000, poll_ms: int = 250) -> dict | None:
    """Block until the Secure Desktop becomes the input desktop, then return the dialog.

    Returns a dict with the UIA tree of the consent dialog plus the extracted
    publisher, or ``None`` if the timeout expires without UAC firing.

    Attaches the calling process to ``WinSta0`` once for the duration of the
    poll loop — the LocalSystem host service starts on ``Service-0x0-3e7$``,
    and ``OpenInputDesktop`` on that station never returns the interactive
    user's input desktop.  Restoring the original window station on exit
    keeps subsequent pipe handlers on their original station.
    """
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    hwinsta_prev = _user32.GetProcessWindowStation()
    hwinsta = _open_winsta0()
    if hwinsta:
        _user32.SetProcessWindowStation(hwinsta)
    logger.info(
        "wait_for_uac_prompt: polling winsta=%s for up to %dms (hwinsta=%s)",
        "WinSta0" if hwinsta else "(failed-open)", timeout_ms, hwinsta,
    )
    seen: dict[str, int] = {}
    try:
        while time.monotonic() < deadline:
            name = ""
            hdesk = _open_input_desktop(_DESKTOP_READOBJECTS)
            if hdesk:
                try:
                    name = _get_desktop_name(hdesk) or ""
                finally:
                    _user32.CloseDesktop(hdesk)
            seen[name] = seen.get(name, 0) + 1
            if name.lower() == "winlogon":
                logger.info("wait_for_uac_prompt: Winlogon detected after %d polls", sum(seen.values()))
                # consent.exe is a user-session process — Session 0 isolation
                # blocks the LocalSystem service from walking its UIA tree even
                # though we're attached to the right desktop. Route the walk
                # through a user-session helper instead (CreateProcessAsUser
                # into the active console session), retrying briefly to absorb
                # consent.exe's paint delay.
                tree: list[dict] = []
                publisher = None
                for attempt in range(8):
                    try:
                        tree = _spawn_in_user_session("tree", timeout=20.0) or []
                    except Exception as exc:
                        logger.warning("user-session tree spawn failed: %s", exc)
                        tree = []
                    try:
                        publisher = _spawn_in_user_session("publisher", timeout=15.0)
                    except Exception as exc:
                        logger.warning("user-session publisher spawn failed: %s", exc)
                        publisher = None
                    if tree:
                        logger.info(
                            "wait_for_uac_prompt: tree captured after %d retries (%d top windows)",
                            attempt, len(tree),
                        )
                        break
                    time.sleep(0.3)
                else:
                    logger.warning(
                        "wait_for_uac_prompt: Winlogon active but user-session UIA tree stayed empty after 8 retries"
                    )
                return {
                    "desktop": "Winlogon",
                    "publisher": publisher,
                    "tree": tree,
                }
            time.sleep(poll_ms / 1000.0)
        logger.warning("wait_for_uac_prompt: timed out; saw desktops: %s", seen)
        return None
    finally:
        if hwinsta:
            _user32.SetProcessWindowStation(hwinsta_prev)
            _user32.CloseWindowStation(hwinsta)
