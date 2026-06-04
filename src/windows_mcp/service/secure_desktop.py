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
    ctypes.wintypes.LPCWSTR,
    ctypes.wintypes.BOOL,
    ctypes.wintypes.DWORD,
]
_user32.OpenWindowStationW.restype = ctypes.wintypes.HANDLE
_user32.OpenDesktopW.argtypes = [
    ctypes.wintypes.LPCWSTR,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.BOOL,
    ctypes.wintypes.DWORD,
]
_user32.OpenDesktopW.restype = ctypes.wintypes.HANDLE
_user32.OpenInputDesktop.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.wintypes.BOOL,
    ctypes.wintypes.DWORD,
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
    ctypes.wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.wintypes.DWORD,
    ctypes.POINTER(ctypes.wintypes.DWORD),
]
_user32.GetUserObjectInformationW.restype = ctypes.wintypes.BOOL
_kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD

# When set (by the user-session worker after reading from broker-passed
# stdin), _input_desktop() attaches the thread to this handle instead of
# trying to OpenDesktopW("Winlogon") itself — which fails for non-SYSTEM
# tokens. See _spawn_in_user_session for the broker side.
_preattached_winlogon_hdesk: int = 0

# When set (by the user-session worker after reading from broker-passed
# stdin), uia_get_tree uses IUIAutomation.ElementFromHandle on this HWND
# instead of walking the thread desktop's root. This is the fallback path
# when the worker can't attach to Winlogon (Winlogon's DACL denies
# OpenDesktopW even to UIAccess processes -- empirically gle=5 ACCESS_DENIED
# on Win11 with TokenUIAccess=1). The SYSTEM broker can open Winlogon and
# enumerate its windows, so it walks Winlogon, finds consent.exe's top HWND,
# and hands it to the worker via stdin. ElementFromHandle works cross-desktop
# for UIAccess processes, so the worker can then walk the dialog without
# ever switching desktops.
_preattached_consent_hwnd: int = 0

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
        logger.info(
            "OpenDesktopW(%r, access=0x%04x) failed (gle=%d)",
            name,
            access,
            gle,
        )
    return handle or 0


def _grant_winlogon_access_to_console_user() -> tuple | None:
    """Temporarily add an ACE granting the active console user
    DESKTOP_ALL_ACCESS to the Winlogon desktop, so the spawned worker can
    OpenDesktopW("Winlogon") + SetThreadDesktop + walk consent.exe.

    Returns a state tuple suitable for handing to _restore_winlogon_dacl,
    or None on failure (caller proceeds without the loosening; tree
    capture will likely return the worker's Default-desktop fallback).
    """
    try:
        import win32api
        import win32security
        import win32ts
    except Exception as exc:  # noqa: BLE001
        logger.info("DACL loosen import failed: %s", exc)
        return None

    DESKTOP_ALL_ACCESS = 0xF01FF  # STANDARD_RIGHTS_REQUIRED | desktop bits
    DACL_SECURITY_INFORMATION = 0x4
    READ_CONTROL = 0x00020000
    WRITE_DAC = 0x00040000
    open_access = DESKTOP_ALL_ACCESS | READ_CONTROL | WRITE_DAC

    try:
        session_id = win32ts.WTSGetActiveConsoleSessionId()
        if session_id in (0xFFFFFFFF, 0):
            logger.info("DACL loosen: no console session")
            return None
        user_token = win32ts.WTSQueryUserToken(session_id)
        user_sid_struct = win32security.GetTokenInformation(
            user_token,
            win32security.TokenUser,
        )
        user_sid = user_sid_struct[0]
        try:
            win32api.CloseHandle(user_token)
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        logger.info("DACL loosen: WTSQueryUserToken/TokenUser failed: %s", exc)
        return None

    hdesk = _user32.OpenDesktopW("Winlogon", 0, False, open_access)
    if not hdesk:
        logger.info(
            "DACL loosen: OpenDesktopW('Winlogon', WRITE_DAC) failed gle=%d",
            ctypes.GetLastError(),
        )
        return None

    try:
        original_sd = win32security.GetUserObjectSecurity(
            hdesk,
            DACL_SECURITY_INFORMATION,
        )
        original_dacl = original_sd.GetSecurityDescriptorDacl()
        new_dacl = win32security.ACL()
        if original_dacl:
            for i in range(original_dacl.GetAceCount()):
                ace = original_dacl.GetAce(i)
                ace_type_flags, mask, sid = ace
                ace_type, _ace_flags = ace_type_flags
                if ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE:
                    new_dacl.AddAccessAllowedAce(
                        win32security.ACL_REVISION,
                        mask,
                        sid,
                    )
                elif ace_type == win32security.ACCESS_DENIED_ACE_TYPE:
                    new_dacl.AddAccessDeniedAce(
                        win32security.ACL_REVISION,
                        mask,
                        sid,
                    )
                # Skip audit/object ACEs -- they don't affect access decisions.
        new_dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            DESKTOP_ALL_ACCESS,
            user_sid,
        )
        new_sd = win32security.SECURITY_DESCRIPTOR()
        new_sd.SetSecurityDescriptorDacl(True, new_dacl, False)
        win32security.SetUserObjectSecurity(
            hdesk,
            DACL_SECURITY_INFORMATION,
            new_sd,
        )
        logger.info(
            "DACL loosen: granted user SID %s DESKTOP_ALL_ACCESS on Winlogon",
            win32security.ConvertSidToStringSid(user_sid),
        )
        return (hdesk, original_sd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("DACL loosen: SetUserObjectSecurity failed: %s", exc)
        try:
            _user32.CloseDesktop(hdesk)
        except Exception:
            pass
        return None


def _restore_winlogon_dacl(state: tuple) -> None:
    """Restore the original Winlogon DACL after _grant_winlogon_access_to_console_user."""
    try:
        import win32security
    except Exception:
        return
    hdesk, original_sd = state
    DACL_SECURITY_INFORMATION = 0x4
    try:
        win32security.SetUserObjectSecurity(
            hdesk,
            DACL_SECURITY_INFORMATION,
            original_sd,
        )
        logger.info("DACL restore: original Winlogon DACL re-applied")
    except Exception as exc:  # noqa: BLE001
        logger.warning("DACL restore failed: %s", exc)
    finally:
        try:
            _user32.CloseDesktop(hdesk)
        except Exception:
            pass


def _get_elevated_user_token_for_impersonation() -> int:
    """Return a token handle the broker can ImpersonateLoggedOnUser with so
    EnumDesktopWindows on Winlogon sees the call from the session-1 admin
    user instead of from SYSTEM session 0. Returns 0 on failure (caller
    skips impersonation and EnumDesktopWindows will return 0 windows due
    to session-isolation, but the rest of the flow still works in the
    degraded path).
    """
    try:
        import win32ts
        import win32security
    except Exception:
        return 0
    try:
        session_id = win32ts.WTSGetActiveConsoleSessionId()
        if session_id in (0xFFFFFFFF, 0):
            return 0
        user_token = win32ts.WTSQueryUserToken(session_id)
        try:
            elevated = win32security.GetTokenInformation(
                user_token,
                win32security.TokenLinkedToken,
            )
        except Exception:
            elevated = None
        # We want to KEEP the elevated handle and CLOSE user_token.
        # win32ts returns PyHANDLE -- int() extracts the underlying handle.
        if elevated:
            try:
                import win32api

                win32api.CloseHandle(user_token)
            except Exception:
                pass
            return int(elevated)
        return int(user_token)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "_get_elevated_user_token_for_impersonation failed: %s",
            exc,
        )
        return 0


def _find_consent_hwnd_on(hdesk: int, impersonate_token: int = 0) -> int:
    """Walk *hdesk* (a Winlogon desktop handle the broker can open as SYSTEM)
    and return the top-level HWND owned by consent.exe -- or 0 if none.

    The user-session worker can't open Winlogon (the desktop DACL denies
    UIAccess processes), so it can't EnumDesktopWindows itself either.
    The SYSTEM broker can, so we do it here and hand the worker just the
    HWND -- ElementFromHandle is cross-desktop with UIAccess.
    """
    _user32.EnumDesktopWindows.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _user32.EnumDesktopWindows.restype = ctypes.wintypes.BOOL
    _user32.GetWindowThreadProcessId.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    _user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
    _user32.GetClassNameW.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.LPWSTR,
        ctypes.c_int,
    ]
    _user32.GetClassNameW.restype = ctypes.c_int
    _kernel32.OpenProcess.argtypes = [
        ctypes.wintypes.DWORD,
        ctypes.wintypes.BOOL,
        ctypes.wintypes.DWORD,
    ]
    _kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    _kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    QueryFullProcessImageNameW = _kernel32.QueryFullProcessImageNameW
    QueryFullProcessImageNameW.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.LPWSTR,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    QueryFullProcessImageNameW.restype = ctypes.wintypes.BOOL

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )

    found = [0]
    enumerated: list[tuple[int, str, str]] = []  # (hwnd, class, exe)

    @WNDENUMPROC
    def _on_window(hwnd, _lparam):
        pid = ctypes.wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cls = ctypes.create_unicode_buffer(128)
        _user32.GetClassNameW(hwnd, cls, 128)
        exe_name = ""
        if pid.value:
            h_proc = _kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                pid.value,
            )
            if h_proc:
                try:
                    buf = ctypes.create_unicode_buffer(260)
                    size = ctypes.wintypes.DWORD(260)
                    if QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                        exe_name = buf.value
                finally:
                    _kernel32.CloseHandle(h_proc)
        enumerated.append((int(hwnd), cls.value, exe_name))
        if exe_name.lower().endswith("\\consent.exe") and found[0] == 0:
            found[0] = int(hwnd)
        return True  # keep enumerating so we capture the full list for diag

    advapi32 = ctypes.windll.advapi32
    advapi32.ImpersonateLoggedOnUser.argtypes = [ctypes.wintypes.HANDLE]
    advapi32.ImpersonateLoggedOnUser.restype = ctypes.wintypes.BOOL
    advapi32.RevertToSelf.restype = ctypes.wintypes.BOOL

    impersonated = False
    if impersonate_token:
        if advapi32.ImpersonateLoggedOnUser(impersonate_token):
            impersonated = True
        else:
            logger.info(
                "_find_consent_hwnd_on: ImpersonateLoggedOnUser failed (gle=%d)",
                ctypes.GetLastError(),
            )
    try:
        ok = _user32.EnumDesktopWindows(
            hdesk,
            ctypes.cast(_on_window, ctypes.c_void_p),
            0,
        )
        enum_gle = ctypes.GetLastError() if not ok else 0
    finally:
        if impersonated:
            advapi32.RevertToSelf()
    logger.info(
        "_find_consent_hwnd_on: EnumDesktopWindows ok=%s gle=%d "
        "windows_seen=%d match=0x%x impersonated=%s",
        bool(ok),
        enum_gle,
        len(enumerated),
        found[0],
        impersonated,
    )
    # Dump up to 20 windows so we can see what was on Winlogon when we looked.
    for hwnd, cls, exe in enumerated[:20]:
        logger.info(
            "_find_consent_hwnd_on:   hwnd=0x%x class=%r exe=%r",
            hwnd,
            cls,
            exe,
        )
    return found[0]


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
            candidates.append(
                ("Winlogon-by-name", lambda access: _open_desktop_by_name("Winlogon", access))
            )
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
            name,
            attached_how,
            attached_via,
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
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "width": w,
                "height": h,
            },
            "center": {"x": rect.left + w // 2, "y": rect.top + h // 2},
            "can_invoke": can_invoke,
            "children": children,
        }
    except Exception as exc:
        logger.debug("_serialize_element error: %s", exc)
        return None


def _diagnose_uia_element(elem: Any, label: str) -> dict:
    """Best-effort metadata dump for one UIA element. Used by the UIAccess
    cross-desktop probes -- the worker's stderr is the only way to see what
    UIA returns for elements that live on Winlogon, so we log enough to
    distinguish consent.exe from the user's Default-desktop fallback.
    """
    info: dict[str, Any] = {"label": label}
    for key, attr in (
        ("name", "CurrentName"),
        ("ctrl", "CurrentLocalizedControlType"),
        ("cls", "CurrentClassName"),
        ("pid", "CurrentProcessId"),
    ):
        try:
            info[key] = getattr(elem, attr)
        except Exception:
            pass
    try:
        hwnd = elem.CurrentNativeWindowHandle
        info["hwnd"] = f"0x{hwnd:x}" if hwnd else "0"
    except Exception:
        pass
    logger.info("uiaccess-probe: %s", info)
    return info


def _find_consent_pid() -> int | None:
    """Walk Toolhelp32Snapshot looking for ``consent.exe``. Returns the first
    matching PID or ``None``. Used to confirm element identity from a
    UIAccess worker that can't OpenDesktop('Winlogon') -- if an element's
    CurrentProcessId matches consent.exe's PID, it is the UAC dialog.
    """
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE or snap == 0:
        return None
    try:
        k32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESSENTRY32W),
        ]
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESSENTRY32W),
        ]
        k32.Process32NextW.restype = wintypes.BOOL
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            if (pe.szExeFile or "").lower() == "consent.exe":
                return int(pe.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)
    return None


def _find_threads_of_pid(pid: int) -> list[int]:
    """Return all thread IDs belonging to ``pid``. Used to call
    ``GetGUIThreadInfo`` against consent.exe's GUI thread directly --
    that API can read cross-desktop thread state for a UIAccess caller
    even when ``OpenDesktop('Winlogon')`` is blocked.
    """
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPTHREAD = 0x00000004
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", ctypes.c_long),
            ("tpDeltaPri", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == INVALID_HANDLE_VALUE or snap == 0:
        return []
    out: list[int] = []
    try:
        k32.Thread32First.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(THREADENTRY32),
        ]
        k32.Thread32First.restype = wintypes.BOOL
        k32.Thread32Next.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(THREADENTRY32),
        ]
        k32.Thread32Next.restype = wintypes.BOOL
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(THREADENTRY32)
        ok = k32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == pid:
                out.append(int(te.th32ThreadID))
            ok = k32.Thread32Next(snap, ctypes.byref(te))
    finally:
        k32.CloseHandle(snap)
    return out


def _walk_to_window_ancestor(elem: Any, walker: Any) -> Any:
    """Walk up an element's ancestors until we hit a Window control (50032)
    or run out of parents. Returns the original element if no Window ancestor
    is reachable -- which is what we want to serialise either way.
    """
    cur = elem
    for _ in range(30):
        try:
            parent = walker.GetParentElement(cur)
        except Exception:
            return cur
        if parent is None:
            return cur
        try:
            ct = parent.CurrentControlType
        except Exception:
            ct = 0
        if ct == 50032:  # UIA_WindowControlTypeId
            return parent
        cur = parent
    return cur


def uia_get_tree_uiaccess(wait_ms: int = 3000) -> list[dict]:
    """Cross-desktop UAC tree fetch using only UIAccess + UIA -- no DACL
    munging, no SetThreadDesktop.

    UIAccess processes are supposed to receive UIA elements/events from any
    desktop (this is the documented mechanism Narrator/Magnifier use to read
    UAC). Iteration 2 layers many strategies, ordered cheap -> expensive:

      S1. ``GetForegroundWindow`` + ``ElementFromHandle``
      S2. ``ElementFromPoint`` at screen centre
      S3. ``GetGUIThreadInfo(0)`` -> hwndActive/hwndFocus
      S4. ``GetGUIThreadInfo(consent.exe-tid)`` for each consent.exe thread
      S5. ``GetFocusedElement``
      S6. Walk top-level children of root via RawViewWalker
      S7-S9. UIA events (Focus, WindowOpened, StructureChanged)

    Each candidate is checked against a consent.exe identity filter (matches
    consent.exe PID, or class/name matches the UAC dialog pattern). Things
    that look like our own user-session windows (ConsoleWindowClass etc.)
    are rejected outright so we don't short-circuit and miss consent.exe.
    """
    import ctypes
    from ctypes import wintypes

    import comtypes

    # --------------------------------------------------------------- helpers
    consent_pid = _find_consent_pid()
    consent_tids = _find_threads_of_pid(consent_pid) if consent_pid else []
    logger.info(
        "uiaccess: consent.exe pid=%s threads=%s",
        consent_pid,
        consent_tids[:6],
    )

    def _info_of(elem: Any) -> dict:
        """Cheap props dump for an element. Logs nothing -- pure read."""
        info: dict[str, Any] = {}
        if elem is None:
            return info
        for key, attr in (
            ("name", "CurrentName"),
            ("ctrl", "CurrentLocalizedControlType"),
            ("cls", "CurrentClassName"),
            ("pid", "CurrentProcessId"),
        ):
            try:
                info[key] = getattr(elem, attr)
            except Exception:
                pass
        try:
            hwnd = elem.CurrentNativeWindowHandle
            info["hwnd"] = int(hwnd) if hwnd else 0
        except Exception:
            info["hwnd"] = 0
        return info

    BAD_CLASSES = {
        "ConsoleWindowClass",
        "ApplicationFrameWindow",
        "CASCADIA_HOSTING_WINDOW_CLASS",
        "PseudoConsoleWindow",
        "WorkerW",
        "Progman",
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "Windows.UI.Core.CoreWindow",
    }
    BAD_NAME_SUBSTRINGS = (
        "powershell",
        "command prompt",
        "windows mcp",
        "task manager",
        "program manager",
        "taskbar",
    )

    def _is_uac_like(info: dict) -> bool:
        if not info:
            return False
        if consent_pid and info.get("pid") == consent_pid:
            return True
        cls = (info.get("cls") or "").lower()
        if "consent" in cls or "credential dialog" in cls or cls == "$$$$markup":
            return True
        name = (info.get("name") or "").lower()
        if "user account control" in name:
            return True
        return False

    def _is_definitely_not_uac(info: dict) -> bool:
        if not info:
            return True
        if (info.get("cls") or "") in BAD_CLASSES:
            return True
        name = (info.get("name") or "").lower()
        if any(s in name for s in BAD_NAME_SUBSTRINGS):
            return True
        return False

    def _work() -> list[dict]:
        iuia, uia_core = _create_uia()
        walker = iuia.RawViewWalker

        def _try_capture(elem: Any, label: str) -> dict | None:
            """Inspect, classify, and serialise an element. Returns the
            serialised tree if and only if it looks like a UAC dialog;
            otherwise logs and returns None so the next strategy can try.
            """
            if elem is None:
                logger.info("uiaccess %s: elem is None", label)
                return None
            info = _info_of(elem)
            logger.info("uiaccess %s: %s", label, info)
            if _is_definitely_not_uac(info):
                logger.info("uiaccess %s: REJECT (cls/name in bad list)", label)
                return None
            top = _walk_to_window_ancestor(elem, walker)
            top_info = _info_of(top) if top is not None else {}
            logger.info("uiaccess %s.top: %s", label, top_info)
            if _is_definitely_not_uac(top_info):
                logger.info("uiaccess %s.top: REJECT (cls/name in bad list)", label)
                return None
            if not (_is_uac_like(info) or _is_uac_like(top_info)):
                logger.info(
                    "uiaccess %s: neutral (not UAC-pid/class/name); skipping",
                    label,
                )
                return None
            node = _serialize_element(top if top is not None else elem, walker)
            if not node:
                logger.info("uiaccess %s: serialise returned empty", label)
                return None
            logger.info(
                "uiaccess %s: UAC-MATCH name=%r kids=%d",
                label,
                node.get("name"),
                len(node.get("children") or []),
            )
            return node

        # ============================================================
        # Strategy S1: GetForegroundWindow + ElementFromHandle
        # ============================================================
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int

        try:
            fg = user32.GetForegroundWindow()
            logger.info("uiaccess S1: GetForegroundWindow=0x%x", fg or 0)
            if fg:
                try:
                    elem = iuia.ElementFromHandle(fg)
                    captured = _try_capture(elem, "S1-foreground")
                    if captured:
                        return [captured]
                except Exception as exc:
                    logger.info("uiaccess S1 ElementFromHandle: %s", exc)
        except Exception as exc:
            logger.info("uiaccess S1: %s", exc)

        # ============================================================
        # Strategy S2: ElementFromPoint at screen centre
        # ============================================================
        try:
            cx = user32.GetSystemMetrics(0) // 2  # SM_CXSCREEN
            cy = user32.GetSystemMetrics(1) // 2
            logger.info("uiaccess S2: ElementFromPoint(%d, %d)", cx, cy)
            try:
                from comtypes.gen.UIAutomationClient import tagPOINT

                pt = tagPOINT(cx, cy)
            except Exception:
                # Fall back to ctypes POINT
                class _POINT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

                pt = _POINT(cx, cy)
            try:
                elem = iuia.ElementFromPoint(pt)
                captured = _try_capture(elem, "S2-center")
                if captured:
                    return [captured]
            except Exception as exc:
                logger.info("uiaccess S2 ElementFromPoint: %s", exc)
        except Exception as exc:
            logger.info("uiaccess S2: %s", exc)

        # ============================================================
        # Strategy S3: GetGUIThreadInfo(0) -- foreground thread's GUI
        # ============================================================
        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        user32.GetGUIThreadInfo.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(GUITHREADINFO),
        ]
        user32.GetGUIThreadInfo.restype = wintypes.BOOL

        def _probe_thread_info(tid: int, tag: str) -> dict | None:
            gti = GUITHREADINFO()
            gti.cbSize = ctypes.sizeof(GUITHREADINFO)
            ok = user32.GetGUIThreadInfo(tid, ctypes.byref(gti))
            logger.info(
                "uiaccess %s: GetGUIThreadInfo(%d) ok=%s active=0x%x focus=0x%x menuOwner=0x%x",
                tag,
                tid,
                bool(ok),
                int(gti.hwndActive or 0),
                int(gti.hwndFocus or 0),
                int(gti.hwndMenuOwner or 0),
            )
            if not ok:
                return None
            for hwnd, sub in (
                (gti.hwndActive, "active"),
                (gti.hwndFocus, "focus"),
                (gti.hwndMenuOwner, "menuOwner"),
            ):
                if hwnd:
                    try:
                        elem = iuia.ElementFromHandle(int(hwnd))
                        cap = _try_capture(elem, f"{tag}-{sub}")
                        if cap:
                            return cap
                    except Exception as exc:
                        logger.info("uiaccess %s-%s EFH: %s", tag, sub, exc)
            return None

        cap = _probe_thread_info(0, "S3")
        if cap:
            return [cap]

        # ============================================================
        # Strategy S4: GetGUIThreadInfo on each consent.exe thread
        # ============================================================
        for tid in consent_tids[:12]:
            cap = _probe_thread_info(tid, f"S4(consent-tid={tid})")
            if cap:
                return [cap]

        # ============================================================
        # Strategy S5: GetFocusedElement (original Strategy A)
        # ============================================================
        try:
            focused = iuia.GetFocusedElement()
            cap = _try_capture(focused, "S5-focused")
            if cap:
                return [cap]
        except Exception as exc:
            logger.info("uiaccess S5 GetFocusedElement: %s", exc)

        # ============================================================
        # Strategy S6: Walk top-level children of root via RawViewWalker
        # ============================================================
        try:
            root = iuia.GetRootElement()
            child = walker.GetFirstChildElement(root)
            i = 0
            while child is not None:
                i += 1
                if i > 80:
                    logger.info("uiaccess S6: stopping at 80 children")
                    break
                cap = _try_capture(child, f"S6-child#{i}")
                if cap:
                    return [cap]
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception as exc:
                    logger.info("uiaccess S6: walker stopped: %s", exc)
                    break
            logger.info("uiaccess S6: walked %d top-level children, no match", i)
        except Exception as exc:
            logger.info("uiaccess S6: %s", exc)

        # ---- Strategies B/C/D: event-based ----
        UIA = uia_core
        cap_lock = threading.Lock()
        captured: list[tuple[str, Any]] = []
        cap_event = threading.Event()

        class _FocusH(comtypes.COMObject):
            _com_interfaces_ = [UIA.IUIAutomationFocusChangedEventHandler]

            def HandleFocusChangedEvent(self, sender):
                try:
                    name = ""
                    try:
                        name = sender.CurrentName or ""
                    except Exception:
                        pass
                    logger.info("uiaccess B focus-event: name=%r", name)
                    with cap_lock:
                        captured.append(("focus", sender))
                    cap_event.set()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("focus handler raised: %s", exc)
                return 0

        class _AutoH(comtypes.COMObject):
            _com_interfaces_ = [UIA.IUIAutomationEventHandler]

            def HandleAutomationEvent(self, sender, event_id):
                try:
                    name = ""
                    try:
                        name = sender.CurrentName or ""
                    except Exception:
                        pass
                    logger.info(
                        "uiaccess C auto-event: id=%d name=%r",
                        event_id,
                        name,
                    )
                    with cap_lock:
                        captured.append(("auto", sender))
                    cap_event.set()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("auto handler raised: %s", exc)
                return 0

        class _StructH(comtypes.COMObject):
            _com_interfaces_ = [UIA.IUIAutomationStructureChangedEventHandler]

            def HandleStructureChangedEvent(self, sender, change_type, runtime_id):
                try:
                    name = ""
                    try:
                        name = sender.CurrentName or ""
                    except Exception:
                        pass
                    logger.info(
                        "uiaccess D struct-event: change=%d name=%r",
                        change_type,
                        name,
                    )
                    with cap_lock:
                        captured.append(("struct", sender))
                    cap_event.set()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("struct handler raised: %s", exc)
                return 0

        focus_h = _FocusH()
        auto_h = _AutoH()
        struct_h = _StructH()

        root = iuia.GetRootElement()
        focus_added = auto_added = struct_added = False
        # 4 = TreeScope_Descendants
        TREE_SCOPE_DESCENDANTS = 4
        # 20016 = UIA_Window_WindowOpenedEventId
        WIN_OPENED_EVENT_ID = 20016

        try:
            try:
                iuia.AddFocusChangedEventHandler(None, focus_h)
                focus_added = True
                logger.info("uiaccess B: AddFocusChangedEventHandler registered")
            except Exception as exc:
                logger.warning("AddFocusChangedEventHandler failed: %s", exc)
            try:
                iuia.AddAutomationEventHandler(
                    WIN_OPENED_EVENT_ID,
                    root,
                    TREE_SCOPE_DESCENDANTS,
                    None,
                    auto_h,
                )
                auto_added = True
                logger.info("uiaccess C: AddAutomationEventHandler(WindowOpened) registered")
            except Exception as exc:
                logger.warning("AddAutomationEventHandler failed: %s", exc)
            try:
                iuia.AddStructureChangedEventHandler(
                    root,
                    TREE_SCOPE_DESCENDANTS,
                    None,
                    struct_h,
                )
                struct_added = True
                logger.info("uiaccess D: AddStructureChangedEventHandler registered")
            except Exception as exc:
                logger.warning("AddStructureChangedEventHandler failed: %s", exc)

            # UIA may deliver events from an MTA worker pool. Our COMObject
            # handlers are STA-registered (via _create_uia's CoInitialize),
            # so the cross-apartment proxy needs the registering thread to
            # pump messages or the call queues forever. We poll cap_event in
            # short slices and pump waiting COM messages between checks --
            # if UIA happens to be in our apartment, this is harmless extra
            # work; if it's cross-apartment, the pump is what makes the
            # handler actually fire.
            try:
                import pythoncom

                pump = pythoncom.PumpWaitingMessages
            except Exception:
                pump = None
            deadline = time.monotonic() + (wait_ms / 1000.0)
            fired = False
            while time.monotonic() < deadline:
                if pump is not None:
                    try:
                        pump()
                    except Exception:
                        pass
                if cap_event.wait(timeout=0.05):
                    fired = True
                    # Keep pumping briefly so subsequent events also land --
                    # callers benefit from a few captured candidates so the
                    # walk_to_window step can pick the most promising one.
                    for _ in range(10):
                        if pump is not None:
                            try:
                                pump()
                            except Exception:
                                pass
                        time.sleep(0.02)
                    break
            with cap_lock:
                logger.info(
                    "uiaccess events: fired=%s captured=%d",
                    fired,
                    len(captured),
                )
                candidates = list(reversed(captured))

            for kind, elem in candidates:
                cap = _try_capture(elem, f"event-{kind}")
                if cap:
                    return [cap]
        finally:
            try:
                if focus_added:
                    iuia.RemoveFocusChangedEventHandler(focus_h)
            except Exception:
                pass
            try:
                if auto_added:
                    iuia.RemoveAutomationEventHandler(
                        WIN_OPENED_EVENT_ID,
                        root,
                        auto_h,
                    )
            except Exception:
                pass
            try:
                if struct_added:
                    iuia.RemoveStructureChangedEventHandler(root, struct_h)
            except Exception:
                pass
            try:
                iuia.RemoveAllEventHandlers()
            except Exception:
                pass

        logger.info("uia_get_tree_uiaccess: all strategies returned empty")
        return []

    try:
        # Give the thread enough headroom for the event wait plus COM teardown.
        return _run_on_fresh_thread(_work, timeout=(wait_ms / 1000.0) + 5.0) or []
    except Exception as exc:
        logger.error("uia_get_tree_uiaccess raised: %s", exc)
        return []


def screenshot_uac_synthetic_tree() -> list[dict]:
    """Capture the secure desktop, locate the UAC dialog by pixel colour,
    return a synthetic UIA tree pinpointing the Yes/No buttons.

    Iteration-3 fallback. Win11 hides consent.exe from every cross-desktop
    UIA / Win32 query we can issue from a UIAccess worker on Default; the
    only thing we *can* observe is what's rendered on the input desktop.
    BitBlt + colour-segmentation finds the focused button by its Microsoft
    Blue (#0067C0) fill, then mirrors across the dialog centre to recover
    the unfocused button. Yes is left, No is right in every Win10/11 UAC.

    Returns ``[]`` if no UAC dialog is visible (no Microsoft Blue pixels).
    Called from the user-session UIAccess worker, which lives on Default
    but whose hardware screen capture lands the secure desktop frame
    because the secure desktop is the currently-active input desktop.
    """
    try:
        from PIL import Image
    except ImportError as exc:
        logger.warning("screenshot_uac_synthetic_tree: PIL unavailable: %s", exc)
        return []

    # Use the broker's capture_screenshot() rather than ImageGrab directly.
    # capture_screenshot wraps SetThreadDesktop(Winlogon) -- a plain
    # ImageGrab.grab() from the user-session worker captures Default's DC
    # and returns "screen grab failed" while UAC is up.
    try:
        png_bytes = capture_screenshot()
        img = Image.open(io.BytesIO(png_bytes))
        img.load()
    except Exception as exc:
        logger.warning("screenshot_uac: capture_screenshot raised: %s", exc)
        return []

    w, h = img.size
    logger.info("screenshot_uac: captured frame %dx%d", w, h)

    # Save the capture for offline diagnostics.
    try:
        share = r"\\host.lan\Data\Windows-MCP\tests\manual\vm_e2e\.work\uac-shot.png"
        img.save(share)
        logger.info("screenshot_uac: dumped to %s", share)
    except Exception as exc:
        logger.info("screenshot_uac: could not dump frame: %s", exc)

    # Find Microsoft-Blue pixels (the focused button's fill colour).
    rgb = img.convert("RGB")
    pixels = rgb.load()
    blue_xs: list[int] = []
    blue_ys: list[int] = []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = pixels[x, y]
            if r < 30 and 80 < g < 130 and 160 < b < 220:
                blue_xs.append(x)
                blue_ys.append(y)

    if not blue_xs:
        logger.info(
            "screenshot_uac: no Microsoft-Blue pixels -- UAC dialog not visible in this frame"
        )
        return []

    foc_x0, foc_x1 = min(blue_xs), max(blue_xs)
    foc_y0, foc_y1 = min(blue_ys), max(blue_ys)
    foc_cx = (foc_x0 + foc_x1) // 2
    btn_w = foc_x1 - foc_x0
    btn_h = foc_y1 - foc_y0
    logger.info(
        "screenshot_uac: blue bbox=(%d,%d,%d,%d) center=(%d,%d) size=%dx%d",
        foc_x0,
        foc_y0,
        foc_x1,
        foc_y1,
        foc_cx,
        (foc_y0 + foc_y1) // 2,
        btn_w,
        btn_h,
    )

    # Mirror across the screen horizontal centre to locate the unfocused
    # button. UAC's default focused button is "No" on Win10/11 (security
    # default), so a blue button on the RIGHT half implies Yes is the
    # mirrored left button. If a future Windows changes the default to
    # focus Yes, the mirroring still works -- we just swap which is which.
    screen_cx = w // 2
    gap = 8  # small inter-button gap, cosmetic
    if foc_cx > screen_cx:
        no_x0, no_y0, no_x1, no_y1 = foc_x0, foc_y0, foc_x1, foc_y1
        yes_x1 = no_x0 - gap
        yes_x0 = yes_x1 - btn_w
        yes_y0, yes_y1 = no_y0, no_y1
    else:
        yes_x0, yes_y0, yes_x1, yes_y1 = foc_x0, foc_y0, foc_x1, foc_y1
        no_x0 = yes_x1 + gap
        no_x1 = no_x0 + btn_w
        no_y0, no_y1 = yes_y0, yes_y1

    logger.info(
        "screenshot_uac: Yes bbox=(%d,%d,%d,%d) No bbox=(%d,%d,%d,%d)",
        yes_x0,
        yes_y0,
        yes_x1,
        yes_y1,
        no_x0,
        no_y0,
        no_x1,
        no_y1,
    )

    def _mk(name, ct, x0, y0, x1, y1, kids=None, invoke=False):
        return {
            "name": name,
            "control_type": ct,
            "bbox": {
                "x": x0,
                "y": y0,
                "width": x1 - x0,
                "height": y1 - y0,
            },
            "can_invoke": invoke,
            "children": kids or [],
        }

    # Dialog bbox: bound the buttons plus a generous padding for the dialog
    # title / body. UAC dialogs are roughly 480x320 on 1280x720 -- we don't
    # need to be exact since downstream consumers only care about the Yes/No
    # bboxes for clicking.
    dlg_x0 = min(yes_x0, no_x0) - 50
    dlg_y0 = max(0, foc_y0 - 300)
    dlg_x1 = max(yes_x1, no_x1) + 50
    dlg_y1 = foc_y1 + 40

    return [
        _mk(
            "User Account Control",
            "window",
            dlg_x0,
            dlg_y0,
            dlg_x1,
            dlg_y1,
            kids=[
                _mk("Yes", "button", yes_x0, yes_y0, yes_x1, yes_y1, invoke=True),
                _mk("No", "button", no_x0, no_y0, no_x1, no_y1, invoke=True),
            ],
        )
    ]


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

    If the broker passed a consent.exe HWND via stdin (the worker can't open
    Winlogon itself; the broker enumerated it as SYSTEM), use
    ElementFromHandle on that HWND -- it's the only path that crosses the
    desktop boundary without SetThreadDesktop, which Winlogon's DACL blocks
    even for UIAccess processes.
    """

    def _work() -> list[dict]:
        nodes: list[dict] = []
        with _input_desktop():
            iuia, _ = _create_uia()
            walker = iuia.RawViewWalker
            roots = []
            if _preattached_consent_hwnd:
                try:
                    elem = iuia.ElementFromHandle(_preattached_consent_hwnd)
                    if elem is not None:
                        logger.info(
                            "uia_get_tree: walking via ElementFromHandle(0x%x)",
                            _preattached_consent_hwnd,
                        )
                        roots.append(elem)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "ElementFromHandle(0x%x) failed: %s; falling back to desktop-root walk",
                        _preattached_consent_hwnd,
                        exc,
                    )
            if not roots:
                root = iuia.GetRootElement()
                child = walker.GetFirstChildElement(root)
                while child:
                    roots.append(child)
                    try:
                        child = walker.GetNextSiblingElement(child)
                    except Exception:
                        break
            for r in roots:
                node = _serialize_element(r, walker)
                if node:
                    nodes.append(node)
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
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.DWORD,
            ctypes.POINTER(ctypes.wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = ctypes.wintypes.BOOL
        advapi32.LookupPrivilegeValueW.argtypes = [
            ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_uint64),
        ]
        advapi32.LookupPrivilegeValueW.restype = ctypes.wintypes.BOOL
        advapi32.AdjustTokenPrivileges.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.BOOL,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
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
            if advapi32.LookupPrivilegeValueW(None, "SeTcbPrivilege", ctypes.byref(luid)):
                # TOKEN_PRIVILEGES { DWORD count; LUID_AND_ATTRIBUTES privs[1]; }
                # LUID_AND_ATTRIBUTES { LUID(8 bytes); DWORD attrs; }
                tp_buf = (ctypes.c_uint32 * 4)()
                tp_buf[0] = 1  # PrivilegeCount
                tp_buf[1] = luid.value & 0xFFFFFFFF  # LUID.LowPart
                tp_buf[2] = (luid.value >> 32) & 0xFFFFFFFF  # LUID.HighPart
                tp_buf[3] = SE_PRIVILEGE_ENABLED  # Attributes
                ok2 = advapi32.AdjustTokenPrivileges(
                    h_proc_token, False, ctypes.byref(tp_buf), 0, None, None
                )
                gle = ctypes.GetLastError() if not ok2 else 0
                logger.info(
                    "AdjustTokenPrivileges(SeTcbPrivilege=ENABLED) ok=%s gle=%d",
                    bool(ok2),
                    gle,
                )
            else:
                logger.warning(
                    "LookupPrivilegeValueW(SeTcbPrivilege) failed gle=%d", ctypes.GetLastError()
                )
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
            ctypes.c_int,  # TOKEN_INFORMATION_CLASS enum
            ctypes.c_void_p,  # LPVOID TokenInformation
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
                "SetTokenInformation(TokenUIAccess=1) on spawn token (handle=0x%x) OK",
                int(spawn_token),
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
    consent_hwnd = 0
    pass_winlogon = bool(op_args) and op_args[0] in ("tree", "publisher")
    if pass_winlogon:
        hdesk_winlogon = _open_desktop_by_name("Winlogon", _DESKTOP_ALL_ACCESS)
        if not hdesk_winlogon:
            logger.warning(
                "broker could not open Winlogon — worker will fall back "
                "to its own enumeration (likely returns wrong desktop)"
            )
        else:
            # The worker can't attach to Winlogon itself, so it can't walk
            # the desktop root. Find consent.exe's top HWND here and pass it
            # to the worker -- ElementFromHandle works cross-desktop with
            # UIAccess and bypasses the SetThreadDesktop requirement.
            #
            # ImpersonateLoggedOnUser with the user's elevated linked token
            # so EnumDesktopWindows on Winlogon doesn't trip session-0/-1
            # isolation (broker is SYSTEM in session 0; without
            # impersonation the kernel refuses to enumerate session-1
            # Winlogon and returns FALSE with windows_seen=0).
            try:
                consent_hwnd = _find_consent_hwnd_on(
                    hdesk_winlogon,
                    impersonate_token=int(spawn_token),
                )
                logger.info(
                    "broker enumerated Winlogon: consent.exe hwnd=0x%x",
                    consent_hwnd,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("_find_consent_hwnd_on failed: %s", exc)

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
    # Spawn on the interactive Default desktop. Tried lpDesktop="winsta0\winlogon"
    # for read ops: even with TokenUIAccess=1 + signed worker in
    # %ProgramFiles%, the OS rejects user32.dll init against Winlogon's
    # DACL and the worker crashes with STATUS_DLL_INIT_FAILED
    # (exit=-1073741502). The worker's _input_desktop later attaches to
    # whichever desktop it can.
    startup.lpDesktop = r"winsta0\default"

    user_env = win32profile.CreateEnvironmentBlock(spawn_token, False)

    creation_flags = win32con.CREATE_NO_WINDOW | win32process.CREATE_UNICODE_ENVIRONMENT

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
        try:
            win32file.CloseHandle(stdout_w)
        except Exception:
            pass
        try:
            win32file.CloseHandle(stderr_w)
        except Exception:
            pass
        try:
            win32file.CloseHandle(stdin_r)
        except Exception:
            pass
        try:
            win32api.CloseHandle(user_token)
        except Exception:
            pass
        if elevated_token:
            try:
                win32api.CloseHandle(elevated_token)
            except Exception:
                pass

    # Hand the worker:
    #   1. (best effort) a duplicated Winlogon desktop handle so it can
    #      SetThreadDesktop directly. HDESK isn't a real kernel handle so
    #      DuplicateHandle almost always fails here with ACCESS_DENIED;
    #      kept as the preferred path in case a future Windows build relaxes
    #      it.
    #   2. (real fallback) the HWND of consent.exe on Winlogon that we
    #      enumerated above. The worker uses ElementFromHandle on this HWND,
    #      which crosses the desktop boundary as long as it has UIAccess.
    handoff_parts: list[str] = []
    if hdesk_winlogon and proc_handle:
        try:
            dup = win32api.DuplicateHandle(
                win32api.GetCurrentProcess(),
                hdesk_winlogon,
                proc_handle,
                0,  # ignored under DUPLICATE_SAME_ACCESS
                False,  # bInheritHandle
                2,  # DUPLICATE_SAME_ACCESS
            )
            handoff_parts.append(f"WINLOGON_HDESK={int(dup)}")
        except Exception as exc:
            logger.warning("Winlogon DuplicateHandle into worker failed: %s", exc)
    if consent_hwnd:
        handoff_parts.append(f"CONSENT_HWND={consent_hwnd}")
    handoff_line = (" ".join(handoff_parts) + "\n") if handoff_parts else "\n"
    try:
        win32file.WriteFile(stdin_w, handoff_line.encode("utf-8"))
    except Exception as exc:
        logger.warning("writing winlogon handoff to worker stdin failed: %s", exc)
    finally:
        try:
            win32file.CloseHandle(stdin_w)
        except Exception:
            pass
        if hdesk_winlogon:
            try:
                _user32.CloseDesktop(hdesk_winlogon)
            except Exception:
                pass

    logger.info(
        "spawned user-session worker pid=? session=%d elevated=%s op=%s winlogon_handoff=%s",
        session_id,
        using_elevated,
        " ".join(op_args),
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
        try:
            win32file.CloseHandle(stdout_r)
        except Exception:
            pass
        try:
            win32file.CloseHandle(stderr_r)
        except Exception:
            pass
        try:
            win32api.CloseHandle(proc_handle)
        except Exception:
            pass
        try:
            win32api.CloseHandle(thread_handle)
        except Exception:
            pass

    stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace").strip()
    stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
    if stderr_text:
        logger.info("user-session worker stderr: %s", stderr_text)

    if not stdout_text:
        raise RuntimeError(
            f"user-session worker produced no stdout (exit={exit_code}, stderr={stderr_text!r})"
        )
    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"user-session worker stdout not JSON (exit={exit_code}): {stdout_text!r}"
        ) from exc
    if not payload.get("ok"):
        raise RuntimeError(f"user-session worker error: {payload.get('error', 'unknown')}")
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
        "WinSta0" if hwinsta else "(failed-open)",
        timeout_ms,
        hwinsta,
    )
    # Diagnostic: log the current PromptOnSecureDesktop registry value so
    # we can tell whether iter-5's "policy was set but UAC still went to
    # Winlogon" is the registry read returning 0 (Windows ignoring the
    # value) or returning 1 (the write didn't actually stick).
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System",
            access=winreg.KEY_QUERY_VALUE,
        ) as _key:
            posd, _ = winreg.QueryValueEx(_key, "PromptOnSecureDesktop")
            elua, _ = winreg.QueryValueEx(_key, "EnableLUA")
            cpba, _ = winreg.QueryValueEx(_key, "ConsentPromptBehaviorAdmin")
        logger.info(
            "wait_for_uac_prompt: registry policy: "
            "PromptOnSecureDesktop=%s EnableLUA=%s ConsentPromptBehaviorAdmin=%s",
            posd,
            elua,
            cpba,
        )
    except Exception as exc:
        logger.warning("wait_for_uac_prompt: could not read UAC policy registry: %s", exc)

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

            # Primary path: PromptOnSecureDesktop=0 (set by `service
            # secure-desktop install`) makes UAC render on the user's
            # Default desktop. consent.exe is then a regular top-level
            # window reachable via plain UIA from a same-session worker --
            # no UIAccess, no DACL loosening, no screenshot trickery.
            # Detect by polling for consent.exe in the process list; the
            # input-desktop name stays "Default" so we can't use it as the
            # trigger here.
            if name.lower() != "winlogon":
                consent_pid = _find_consent_pid()
                if consent_pid:
                    logger.info(
                        "wait_for_uac_prompt: consent.exe pid=%d on desktop=%r -- using Default-desktop UIA path",
                        consent_pid,
                        name,
                    )
                    tree: list[dict] = []
                    publisher = None
                    try:
                        tree = _spawn_in_user_session("tree", timeout=10.0) or []
                    except Exception as exc:
                        logger.warning("Default-desktop tree spawn failed: %s", exc)
                        tree = []
                    try:
                        publisher = _spawn_in_user_session("publisher", timeout=10.0)
                    except Exception as exc:
                        logger.warning("publisher spawn failed: %s", exc)
                        publisher = None
                    return {
                        "desktop": name or "Default",
                        "publisher": publisher,
                        "tree": tree,
                    }

            if name.lower() == "winlogon":
                logger.info(
                    "wait_for_uac_prompt: Winlogon detected after %d polls", sum(seen.values())
                )
                # Two paths to recover the consent.exe tree:
                #
                #   1. UIAccess-only via UIA events / GetFocusedElement -- no
                #      desktop attach, no DACL change. Cheap and the only
                #      path that gets to 4/4 on Win11 without weakening
                #      Winlogon's DACL. Documented behaviour: a UIAccess
                #      process receives UIA elements cross-desktop, which is
                #      how Narrator reads UAC.
                #
                #   2. DACL-loosening fallback -- temporarily ACE the console
                #      user onto Winlogon's DACL, spawn the worker which can
                #      now OpenDesktopW("Winlogon") + SetThreadDesktop + walk
                #      consent.exe, then restore. Path of last resort; opens
                #      a brief security regression and we only take it if (1)
                #      fails.
                tree: list[dict] = []
                publisher = None
                try:
                    tree = (
                        _spawn_in_user_session(
                            "tree_uiaccess",
                            "--wait-ms=5000",
                            timeout=15.0,
                        )
                        or []
                    )
                except Exception as exc:
                    logger.warning(
                        "wait_for_uac_prompt: tree_uiaccess raised: %s",
                        exc,
                    )
                    tree = []
                if tree:
                    logger.info(
                        "wait_for_uac_prompt: UIAccess strategy returned %d top windows -- skipping DACL loosen",
                        len(tree),
                    )
                    try:
                        publisher = _spawn_in_user_session("publisher", timeout=10.0)
                    except Exception as exc:
                        logger.warning("publisher spawn failed: %s", exc)
                        publisher = None
                    return {
                        "desktop": "Winlogon",
                        "publisher": publisher,
                        "tree": tree,
                    }

                # Iteration 3 fallback: every UIA/Win32 cross-desktop query
                # we know of returns nothing from a UIAccess worker on Win11.
                # Capture the rendered secure desktop in the BROKER (its
                # SetThreadDesktop attaches to Winlogon; the worker can't)
                # and locate the dialog buttons by colour.
                logger.info(
                    "wait_for_uac_prompt: UIAccess strategy empty -- trying screenshot fallback"
                )
                try:
                    tree = screenshot_uac_synthetic_tree() or []
                except Exception as exc:
                    logger.warning(
                        "wait_for_uac_prompt: screenshot_uac_synthetic_tree raised: %s",
                        exc,
                    )
                    tree = []
                if tree:
                    logger.info(
                        "wait_for_uac_prompt: screenshot fallback returned %d top windows",
                        len(tree),
                    )
                    try:
                        publisher = _spawn_in_user_session("publisher", timeout=10.0)
                    except Exception as exc:
                        logger.warning("publisher spawn failed: %s", exc)
                        publisher = None
                    return {
                        "desktop": "Winlogon",
                        "publisher": publisher,
                        "tree": tree,
                    }

                logger.info(
                    "wait_for_uac_prompt: screenshot fallback empty -- "
                    "falling back to DACL-loosen + desktop-attach"
                )
                dacl_state = _grant_winlogon_access_to_console_user()
                try:
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
                                attempt,
                                len(tree),
                            )
                            break
                        time.sleep(0.3)
                    else:
                        logger.warning(
                            "wait_for_uac_prompt: Winlogon active but user-session UIA tree stayed empty after 8 retries"
                        )
                finally:
                    if dacl_state:
                        _restore_winlogon_dacl(dacl_state)
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
