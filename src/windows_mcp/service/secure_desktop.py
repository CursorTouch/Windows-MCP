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
import logging
import re
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

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

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
def _input_desktop():
    """Switch process/thread to WinSta0\\<input desktop>, then restore on exit."""
    hwinsta_prev = _user32.GetProcessWindowStation()
    hdesk_prev = _user32.GetThreadDesktop(_kernel32.GetCurrentThreadId())
    hwinsta = _open_winsta0()
    if hwinsta:
        _user32.SetProcessWindowStation(hwinsta)
    hdesk = _open_input_desktop(_DESKTOP_ALL_ACCESS)
    if hdesk:
        _user32.SetThreadDesktop(hdesk)
    try:
        yield
    finally:
        if hdesk:
            _user32.SetThreadDesktop(hdesk_prev)
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
                # consent.exe paints its window a few hundred ms after the input
                # desktop flips to Winlogon. Retry the UIA walk until it sees at
                # least one child, or 1.5 s elapses — beyond that the tree is
                # really empty.
                tree: list[dict] = []
                publisher = None
                for attempt in range(8):
                    tree = uia_get_tree() or []
                    publisher = get_uac_publisher()
                    if tree:
                        logger.info("wait_for_uac_prompt: tree captured after %d retries (%d top windows)", attempt, len(tree))
                        break
                    time.sleep(0.2)
                else:
                    logger.warning("wait_for_uac_prompt: Winlogon active but UIA tree stayed empty after 8 retries")
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
