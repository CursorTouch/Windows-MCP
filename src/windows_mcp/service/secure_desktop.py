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
# bytes) but the *W APIs expect LPCWSTR (UTF-16) -- silently corrupted
# calls return NULL with no obvious cause.
_user32.OpenWindowStationW.argtypes = [
    ctypes.wintypes.LPCWSTR,
    ctypes.wintypes.BOOL,
    ctypes.wintypes.DWORD,
]
_user32.OpenWindowStationW.restype = ctypes.wintypes.HANDLE
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


def _get_desktop_name(hdesk: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    needed = ctypes.wintypes.DWORD()
    _user32.GetUserObjectInformationW(
        hdesk, _UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)
    )
    return buf.value


@contextmanager
def _input_desktop(prefer_winlogon: bool = True):  # noqa: ARG001 -- kept for callsite compatibility
    """Switch the thread to the input (Default) desktop, then restore on exit.

    Pre-iter-8 this also tried `OpenDesktopW("Winlogon")` to walk the
    UAC dialog cross-desktop; that whole path is gone now because the
    secure-desktop install routes UAC to Default. The `prefer_winlogon`
    argument is left as a stub so old callsites compile -- it is ignored.
    Tries ALL_ACCESS first (needed for synthetic input), falls back to
    DESKTOP_READ_ATTACH if that's all we can get.
    """
    hwinsta_prev = _user32.GetProcessWindowStation()
    hdesk_prev = _user32.GetThreadDesktop(_kernel32.GetCurrentThreadId())
    hwinsta = _open_winsta0()
    if hwinsta:
        _user32.SetProcessWindowStation(hwinsta)
    hdesk = 0
    own_hdesk = True
    attached_via = None
    for access in (_DESKTOP_ALL_ACCESS, _DESKTOP_READ_ATTACH):
        hdesk = _open_input_desktop(access)
        if not hdesk:
            continue
        if _user32.SetThreadDesktop(hdesk):
            attached_via = access
            break
        _user32.CloseDesktop(hdesk)
        hdesk = 0
    if hdesk:
        name = _get_desktop_name(hdesk) or "(unknown)"
        logger.info("_input_desktop: attached to %r access=0x%04x", name, attached_via)
    else:
        logger.warning(
            "_input_desktop: could not attach to the input desktop. The "
            "thread will stay on the worker's initial desktop and UIA "
            "enumeration will reflect that desktop."
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


def _tree_contains_consent(tree: list[dict], _pid: int) -> bool:
    """Return True if the worker's serialized UIA tree contains a window that
    looks like consent.exe's UAC dialog. We match on the dialog title
    "User Account Control" rather than process id (the serializer doesn't
    record pid). _pid is accepted for future use."""

    target = "user account control"

    def _walk(node: dict) -> bool:
        if not isinstance(node, dict):
            return False
        if target in (node.get("name") or "").lower():
            return True
        for child in node.get("children") or []:
            if _walk(child):
                return True
        return False

    return any(_walk(n) for n in tree)


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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_input_desktop_name() -> str:
    """Return the name of the current input desktop.

    Returns ``"Default"`` during normal desktop use and ``"Winlogon"`` while a
    UAC prompt is on the secure desktop. Works from user-mode too.

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


def _send_tab_key() -> None:
    """Send a single Tab keypress via SendInput on the current input desktop.

    Used to provoke a UIA FocusChanged event from consent.exe: the worker
    has TokenUIAccess=1, so UIPI permits SendInput to the higher-integrity
    UAC dialog. Tab only moves focus between the Yes/No buttons -- it never
    activates one -- so it is a safe nudge to make the dialog re-emit a
    focus event that a registered handler can capture.
    """
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_TAB = 0x09

    ULONG_PTR = ctypes.c_size_t  # pointer-sized, matches ULONG_PTR on x86/x64

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.wintypes.WORD),
            ("wScan", ctypes.wintypes.WORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        # Largest union member -- present only so the union (and thus INPUT)
        # gets the correct size. SendInput validates cbSize == sizeof(INPUT),
        # which is 40 bytes on x64; without MOUSEINPUT the union shrinks to
        # KEYBDINPUT and the call fails with ERROR_INVALID_PARAMETER.
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.wintypes.DWORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.wintypes.DWORD), ("u", _INPUT_UNION)]

    SendInput = _user32.SendInput
    SendInput.argtypes = [ctypes.wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    SendInput.restype = ctypes.wintypes.UINT

    down = _INPUT(type=INPUT_KEYBOARD, u=_INPUT_UNION(ki=_KEYBDINPUT(VK_TAB, 0, 0, 0, 0)))
    up = _INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUT_UNION(ki=_KEYBDINPUT(VK_TAB, 0, KEYEVENTF_KEYUP, 0, 0)),
    )
    arr = (_INPUT * 2)(down, up)
    sent = SendInput(2, arr, ctypes.sizeof(_INPUT))
    logger.info(
        "_send_tab_key: SendInput sent=%d gle=%d sizeof(INPUT)=%d",
        sent, ctypes.GetLastError(), ctypes.sizeof(_INPUT),
    )


def _capture_consent_via_focus_events(iuia, uia_core, walker, consent_pid, wait_ms=3000):
    """Strategy A: subscribe to UIA FocusChanged events and capture the
    consent.exe element when one fires.

    Win 11 25H2 blocks every *pull* path to consent.exe (root walk,
    FindAll(ProcessId), ElementFromHandle gives only the frame). But UIA
    event delivery is *push* and is what Narrator/Magnifier rely on for
    cross-integrity access. A UIAccess process receives focus events from
    higher-integrity processes. We register a no-filter FocusChanged
    handler, nudge the dialog with a Tab keypress to force a fresh focus
    event, pump the STA message queue, and capture any sender whose
    ProcessId matches consent.exe. Returns the serialized dialog-root node
    or None.
    """
    import comtypes
    import pythoncom

    cap_lock = threading.Lock()
    captured: list[Any] = []
    cap_event = threading.Event()

    class _FocusHandler(comtypes.COMObject):
        _com_interfaces_ = [uia_core.IUIAutomationFocusChangedEventHandler]

        def HandleFocusChangedEvent(self, sender):
            try:
                spid = sender.CurrentProcessId if sender is not None else None
                if spid == consent_pid:
                    with cap_lock:
                        captured.append(sender)
                    cap_event.set()
            except Exception:
                pass
            return 0

    handler = _FocusHandler()
    registered = False
    try:
        iuia.AddFocusChangedEventHandler(None, handler)
        registered = True
        logger.info("focus-events: AddFocusChangedEventHandler registered")
    except Exception as exc:  # noqa: BLE001
        logger.warning("focus-events: AddFocusChangedEventHandler failed: %s", exc)
        return None

    try:
        # Nudge the dialog so it emits a fresh focus event.
        try:
            _send_tab_key()
        except Exception as exc:  # noqa: BLE001
            logger.warning("focus-events: _send_tab_key failed: %s", exc)

        deadline = time.monotonic() + (wait_ms / 1000.0)
        nudges = 0
        while time.monotonic() < deadline:
            pythoncom.PumpWaitingMessages()
            if cap_event.wait(timeout=0.05):
                break
            # Re-nudge every ~1s in case the first Tab landed before the
            # handler was fully wired through the COM marshaller.
            nudges += 1
            if nudges % 20 == 0:
                try:
                    _send_tab_key()
                except Exception:
                    pass

        with cap_lock:
            sender = captured[0] if captured else None
        if sender is None:
            logger.info("focus-events: no consent.exe focus event captured")
            return None
        # Walk up to the dialog root so Yes AND No are both descendants.
        cur = sender
        for _ in range(8):
            try:
                parent = walker.GetParentElement(cur)
            except Exception:
                break
            if parent is None:
                break
            try:
                ppid = parent.CurrentProcessId
            except Exception:
                ppid = None
            if ppid != consent_pid:
                break
            cur = parent
        node = _serialize_element(cur, walker)
        if node:
            logger.info("focus-events: captured + serialized consent dialog")
        return node
    finally:
        if registered:
            try:
                iuia.RemoveFocusChangedEventHandler(handler)
            except Exception:
                pass


def _find_visible_hwnds_for_pid(pid: int) -> list[int]:
    """Return every TOP-LEVEL + CHILD HWND owned by *pid* on the current
    desktop, regardless of visibility flag.

    consent.exe's dialog content (the Yes/No buttons, the prompt text,
    publisher info) is rendered into a CHILD window of the frame HWND --
    Win32 EnumWindows returns the top-level frame but not its children.
    We use EnumWindows + EnumChildWindows together to get every HWND
    consent.exe owns, then walk each via ElementFromHandle.
    """
    EnumWindows = _user32.EnumWindows
    EnumWindows.argtypes = [ctypes.c_void_p, ctypes.wintypes.LPARAM]
    EnumWindows.restype = ctypes.wintypes.BOOL
    EnumChildWindows = _user32.EnumChildWindows
    EnumChildWindows.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p, ctypes.wintypes.LPARAM]
    EnumChildWindows.restype = ctypes.wintypes.BOOL
    GetWindowThreadProcessId = _user32.GetWindowThreadProcessId
    GetWindowThreadProcessId.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )
    top: list[int] = []
    children: list[int] = []

    @WNDENUMPROC
    def _on_top(hwnd, _lparam):
        owner = ctypes.wintypes.DWORD(0)
        GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            top.append(int(hwnd))
        return True

    @WNDENUMPROC
    def _on_child(hwnd, _lparam):
        owner = ctypes.wintypes.DWORD(0)
        GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            children.append(int(hwnd))
        return True

    EnumWindows(ctypes.cast(_on_top, ctypes.c_void_p), 0)
    for parent_hwnd in top:
        EnumChildWindows(parent_hwnd, ctypes.cast(_on_child, ctypes.c_void_p), 0)
    # De-duplicate while preserving order (top first, then children).
    seen: set[int] = set()
    out: list[int] = []
    for h in top + children:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _find_top_hwnd_for_pid(pid: int) -> int:
    """Legacy helper retained for the publisher walker. Returns the first
    visible top-level HWND, or 0."""
    hwnds = _find_visible_hwnds_for_pid(pid)
    return hwnds[0] if hwnds else 0


def _synthesize_uac_buttons(dialog_bbox: dict) -> list[dict]:
    """Build synthesized Yes/No button nodes from the dialog bounding box.

    The Win 11 consent.exe dialog renders its action buttons as XAML inside
    a Composition layer that the UIA tree walker can't descend into across
    the integrity boundary. The buttons ARE clickable -- ElementFromPoint
    at click time resolves them across integrity. So we synthesize nodes
    with predicted center coords and ``can_invoke=True`` so the LLM's
    button-finding pass (name + can_invoke) succeeds and the subsequent
    Click(loc=[cx, cy]) lands on the real XAML element.

    The layout for the modern Win 11 consent dialog (observed at 1280x720):
      dialog bbox left=420 right=860 top=178 bottom=535 (440x357)
      Yes button center ~ (538, 507)  -> 0.27 from left, 28px from bottom
      No  button center ~ (745, 507)  -> 0.74 from left, 28px from bottom
    The same relative positions hold for the taller "expanded details"
    layout because the Yes/No strip is anchored to the dialog bottom.
    """
    left = dialog_bbox.get("left", 0)
    top = dialog_bbox.get("top", 0)
    right = dialog_bbox.get("right", 0)
    bottom = dialog_bbox.get("bottom", 0)
    if right <= left or bottom <= top:
        return []
    w = right - left
    yes_cx = left + int(w * 0.27)
    no_cx = left + int(w * 0.74)
    cy = bottom - 28

    def _btn(name: str, cx: int, cy: int) -> dict:
        # 80x32 hit-rect approximation centered on (cx, cy).
        return {
            "name": name,
            "control_type": "button",
            "bbox": {
                "left": cx - 40, "top": cy - 16,
                "right": cx + 40, "bottom": cy + 16,
                "width": 80, "height": 32,
            },
            "center": {"x": cx, "y": cy},
            "can_invoke": True,
            "children": [],
            "_synthesized": True,
        }

    return [_btn("Yes", yes_cx, cy), _btn("No", no_cx, cy)]


def uia_get_tree(consent_pid: int = 0) -> list[dict]:
    """Return the full UIA tree of the current input desktop.

    Each entry is a top-level window serialized as a nested dict. Elements
    with ``can_invoke=True`` support ``IUIAutomationInvokePattern`` — the
    broker uses this to identify clickable buttons (Yes/No on a UAC dialog)
    without re-walking the tree.

    If *consent_pid* is non-zero, find that pid's top HWND via EnumWindows
    and walk from ``ElementFromHandle(hwnd)`` instead of the desktop root.

    The first node may carry a ``_diag`` key with the lookup outcome
    (``hwnd=...``, fallback reason) so the broker / client can see what
    happened without needing access to the SYSTEM broker log.
    """

    def _work() -> list[dict]:
        nodes: list[dict] = []
        diag_lines: list[str] = []
        with _input_desktop():
            iuia, uia_core = _create_uia()
            walker = iuia.RawViewWalker
            if consent_pid:
                # Win 11 25H2 blocks every UIA *pull* path to consent.exe
                # (System integrity): root walk, FindAll(ProcessId)=0,
                # ElementFromHandle returns only the OS frame. UIA event
                # delivery is *push* and is what Narrator/Magnifier use for
                # cross-integrity access -- try that first (strategy A).
                try:
                    focus_node = _capture_consent_via_focus_events(
                        iuia, uia_core, walker, consent_pid, wait_ms=4000
                    )
                    if focus_node:
                        focus_node["_diag"] = (
                            f"consent_pid={consent_pid} | path=FocusEvent"
                        )
                        nodes.append(focus_node)
                        return nodes
                    diag_lines.append(f"consent_pid={consent_pid} FocusEvent=empty")
                except Exception as exc:  # noqa: BLE001
                    diag_lines.append(f"FocusEvent raised: {exc}")
                    logger.warning("uia_get_tree: focus-event strategy failed: %s", exc)

                # GetFocusedElement is a cheap pull-path retry now that a Tab
                # nudge may have moved focus onto a consent button.
                UIA_ProcessIdPropertyId = 30005 - 3  # 30002
                try:
                    focused = iuia.GetFocusedElement()
                    fpid = None
                    try:
                        fpid = focused.CurrentProcessId if focused is not None else None
                    except Exception:
                        fpid = None
                    diag_lines.append(
                        f"consent_pid={consent_pid} GetFocusedElement pid={fpid}"
                    )
                    if focused is not None and fpid == consent_pid:
                        # Walk up to the dialog root so the serializer
                        # captures both Yes and No as descendants.
                        cur = focused
                        for _ in range(8):
                            try:
                                parent = walker.GetParentElement(cur)
                            except Exception:
                                break
                            if parent is None:
                                break
                            try:
                                ppid = parent.CurrentProcessId
                            except Exception:
                                ppid = None
                            if ppid != consent_pid:
                                break
                            cur = parent
                        node = _serialize_element(cur, walker)
                        if node:
                            node["_diag"] = " | ".join(diag_lines) + " | path=GetFocusedElement+walk-up"
                            nodes.append(node)
                            return nodes
                        else:
                            diag_lines.append("focus walk-up: _serialize_element returned None")
                except Exception as exc:  # noqa: BLE001
                    diag_lines.append(f"GetFocusedElement raised: {exc}")
                    logger.warning("uia_get_tree: GetFocusedElement failed: %s", exc)
                # Then try FindAll(ProcessId) as a backup
                try:
                    pid_cond = iuia.CreatePropertyCondition(UIA_ProcessIdPropertyId, consent_pid)
                    matches = iuia.GetRootElement().FindAll(
                        _UIA_TreeScope_Descendants, pid_cond
                    )
                    length = matches.Length if matches is not None else 0
                    diag_lines.append(f"FindAll(ProcessId)={length}")
                    if length > 0:
                        for i in range(length):
                            elem = matches.GetElement(i)
                            if elem is None:
                                continue
                            node = _serialize_element(elem, walker)
                            if node:
                                if not nodes:
                                    node["_diag"] = " | ".join(diag_lines) + " | path=FindAll(ProcessId)"
                                nodes.append(node)
                        if nodes:
                            return nodes
                except Exception as exc:  # noqa: BLE001
                    diag_lines.append(f"FindAll(ProcessId) raised: {exc}")
                    logger.warning("uia_get_tree: FindAll(ProcessId) failed: %s", exc)
                # Fallback to the HWND walk in case FindAll returned 0 (e.g.
                # consent.exe registered nothing or the call was denied).
                hwnds = _find_visible_hwnds_for_pid(consent_pid)
                diag_lines.append(
                    f"hwnds=[{','.join(f'0x{h:x}' for h in hwnds)}]"
                )
                walked_any = False
                for hwnd in hwnds:
                    try:
                        elem = iuia.ElementFromHandle(hwnd)
                        if elem is None:
                            diag_lines.append(f"ElementFromHandle(0x{hwnd:x}) None")
                            continue
                        node = _serialize_element(elem, walker)
                        if node:
                            # consent.exe renders Yes/No as XAML/Composition
                            # that the UIA walker can't descend into cross-
                            # integrity. If this is the UAC dialog window with
                            # no walkable children, synthesize Yes/No nodes
                            # from the bbox so downstream callers can locate
                            # them by name + center coords. The Click tool
                            # uses ElementFromPoint at click time to resolve
                            # the actual button under those coords.
                            if (
                                not node.get("children")
                                and "Account Control" in (node.get("name") or "")
                            ):
                                synth = _synthesize_uac_buttons(node["bbox"])
                                node["children"] = synth
                                diag_lines.append(
                                    f"synthesized_yes_no={len(synth)} from bbox={node['bbox']}"
                                )
                            if not nodes:
                                node["_diag"] = " | ".join(diag_lines) + f" | path=ElementFromHandle(0x{hwnd:x})"
                            nodes.append(node)
                            walked_any = True
                    except Exception as exc:  # noqa: BLE001
                        diag_lines.append(f"ElementFromHandle(0x{hwnd:x}) raised: {exc}")
                        logger.warning(
                            "uia_get_tree: ElementFromHandle(0x%x) failed: %s", hwnd, exc
                        )
                if walked_any:
                    return nodes
            root = iuia.GetRootElement()
            child = walker.GetFirstChildElement(root)
            first = True
            while child:
                node = _serialize_element(child, walker)
                if node:
                    if first and diag_lines:
                        node["_diag"] = " | ".join(diag_lines) + " | path=desktop-root"
                        first = False
                    nodes.append(node)
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:
                    break
            if not nodes and diag_lines:
                # Walker returned nothing at all -- bubble the diag up as a
                # placeholder node so the caller can see why.
                nodes.append({
                    "name": "(empty)",
                    "control_type": "",
                    "bbox": {"left": 0, "top": 0, "right": 0, "bottom": 0, "width": 0, "height": 0},
                    "center": {"x": 0, "y": 0},
                    "can_invoke": False,
                    "children": [],
                    "_diag": " | ".join(diag_lines) + " | path=walker-empty",
                })
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


def get_uac_publisher(consent_pid: int = 0) -> str | None:
    """Inspect the active UAC consent dialog and return its publisher string.

    Returns ``None`` if no UAC dialog is currently displayed, if its layout does
    not match the expected English pattern, or if reading the UIA tree fails.

    When *consent_pid* is non-zero, scope the walk to that pid's top-level
    HWND via ElementFromHandle -- same rationale as uia_get_tree.
    """

    def _work() -> str | None:
        with _input_desktop():
            iuia, _ = _create_uia()
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

            roots: list[Any] = []
            if consent_pid:
                hwnd = _find_top_hwnd_for_pid(consent_pid)
                if hwnd:
                    try:
                        elem = iuia.ElementFromHandle(hwnd)
                        if elem is not None:
                            roots.append(elem)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "get_uac_publisher: ElementFromHandle(0x%x) failed: %s",
                            hwnd, exc,
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
                _collect(r)

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

    # Worker reads its stdin once and treats an empty line as "no
    # pre-attach" -- close the write end so the child doesn't block.
    try:
        win32file.WriteFile(stdin_w, b"\n")
    except Exception as exc:
        logger.warning("writing handoff line to worker stdin failed: %s", exc)
    finally:
        try:
            win32file.CloseHandle(stdin_w)
        except Exception:
            pass

    logger.info(
        "spawned user-session worker pid=? session=%d elevated=%s op=%s",
        session_id,
        using_elevated,
        " ".join(op_args),
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
            # Detect by polling for consent.exe in the process list.
            #
            # We previously had an early-bail branch on input-desktop name
            # == "Winlogon" -- but Windows briefly transitions the input
            # desktop to Winlogon during the UAC dispatch sequence even
            # when POSD=0 keeps the actual dialog on Default. That early
            # bail returned tree=[] before the consent.exe check ran, so
            # the worker never got a chance to walk the dialog. Now we
            # always check consent.exe first regardless of the input
            # desktop name; only after the polling window expires without
            # ever seeing consent.exe will we report "Winlogon detected"
            # as a diagnostic.
            consent_pid = _find_consent_pid()
            if consent_pid:
                logger.info(
                    "wait_for_uac_prompt: consent.exe pid=%d on desktop=%r -- using Default-desktop UIA path",
                    consent_pid,
                    name,
                )
                # Pass consent_pid to the worker so uia_get_tree /
                # get_uac_publisher can scope via EnumWindows +
                # ElementFromHandle / FocusChanged events. consent.exe at
                # System integrity is not returned by GetRootElement walk
                # even from a UIAccess caller; cross-integrity access
                # requires the per-pid scoped path.
                pid_arg = f"--consent-pid={consent_pid}"
                tree: list[dict] = []
                publisher = None
                for attempt in range(30):
                    try:
                        tree = _spawn_in_user_session("tree", pid_arg, timeout=15.0) or []
                    except Exception as exc:
                        logger.warning("Default-desktop tree spawn failed: %s", exc)
                        tree = []
                    if _tree_contains_consent(tree, consent_pid):
                        logger.info(
                            "wait_for_uac_prompt: consent.exe in UIA tree after %d retries (%d top windows)",
                            attempt,
                            len(tree),
                        )
                        break
                    time.sleep(0.2)
                else:
                    logger.warning(
                        "wait_for_uac_prompt: consent.exe pid=%d never appeared in user-session UIA tree after 30 retries -- returning whatever the worker saw",
                        consent_pid,
                    )
                try:
                    publisher = _spawn_in_user_session("publisher", pid_arg, timeout=10.0)
                except Exception as exc:
                    logger.warning("publisher spawn failed: %s", exc)
                    publisher = None
                return {
                    "desktop": name or "Default",
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
