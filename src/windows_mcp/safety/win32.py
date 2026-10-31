"""Windows backends for the safety layer (ctypes/psutil, imported lazily).

Everything here is a thin call into user32/psutil so that the policy code can be
unit-tested with a fake platform object on any OS.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

from windows_mcp.safety.launch import ShortcutInfo

logger = logging.getLogger(__name__)

MB_YESNO = 0x00000004
MB_ICONWARNING = 0x00000030
MB_DEFBUTTON2 = 0x00000100
MB_SYSTEMMODAL = 0x00001000
MB_SETFOREGROUND = 0x00010000
MB_TOPMOST = 0x00040000
IDYES = 6
MB_TIMEDOUT = 32000
GA_ROOT = 2
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
_MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def process_name(pid: int | None) -> str | None:
    """Return the image name (e.g. ``notepad.exe``) of *pid*, or None."""
    if not pid:
        return None
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:
        return None


def _pid_of_hwnd(hwnd) -> int | None:
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def foreground_pid() -> int | None:
    """PID owning the foreground window."""
    return _pid_of_hwnd(_user32().GetForegroundWindow())


def pid_at_point(x: int, y: int) -> int | None:
    """PID owning the top-level window under screen point (x, y)."""
    user32 = _user32()
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.restype = wintypes.HWND
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    root = user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None
    return _pid_of_hwnd(root or hwnd)


def pids_for_window_title(fragment: str) -> list[int]:
    """PIDs of visible top-level windows whose title contains *fragment* (case-insensitive)."""
    user32 = _user32()
    found: list[int] = []
    needle = fragment.lower()
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if needle in buf.value.lower():
                    pid = _pid_of_hwnd(hwnd)
                    if pid:
                        found.append(pid)
        return True

    user32.EnumWindows(proc(callback), 0)
    return found


def _dialog_on_thread(user32, thread_id: int) -> int | None:
    """Return the message-box window (class #32770) owned by *thread_id*, if any."""
    found: list[int] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(16)
        user32.GetClassNameW(hwnd, name, 16)
        if name.value == "#32770" and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True

    user32.EnumThreadWindows(thread_id, proc(visit), 0)
    return found[0] if found else None


def _force_foreground(user32, hwnd: int, send_alt: Callable[[], None]) -> bool:
    """Give *hwnd* foreground and keyboard focus despite the foreground lock.

    SetForegroundWindow is refused unless the caller is allowed to move the
    foreground (Microsoft docs: e.g. it received the last input event). Attaching
    to the current foreground thread's input, then if needed sending an injected
    Alt tap so this process owns the last input event, satisfies that rule.
    """
    if user32.GetForegroundWindow() == hwnd:
        return True
    for attempt in range(2):
        if attempt:
            send_alt()
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        here = ctypes.WinDLL("kernel32").GetCurrentThreadId()
        attached = bool(fg_thread and fg_thread != here) and bool(
            user32.AttachThreadInput(here, fg_thread, True)
        )
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(here, fg_thread, False)
        if user32.GetForegroundWindow() == hwnd:
            return True
    return False


def _send_alt_tap() -> None:
    """Inject Alt down/up (no character, no menu on a message box)."""
    user32 = _user32()
    VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)


def bring_dialog_forward(
    find: Callable[[], int | None],
    force: Callable[[int], bool],
    stop: threading.Event,
    wait_seconds: float = 3.0,
    poll_seconds: float = 0.02,
) -> bool:
    """Wait for the dialog window to appear, then make it the keyboard target."""
    deadline = time.monotonic() + wait_seconds
    while not stop.is_set() and time.monotonic() < deadline:
        hwnd = find()
        if hwnd:
            if force(hwnd):
                return True
            logger.warning("Confirmation dialog could not take keyboard focus; click it")
            return False
        stop.wait(poll_seconds)
    return False


def confirm_dialog(title: str, text: str, timeout_seconds: float) -> bool:
    """Show a native, topmost Yes/No dialog. True only for an explicit Yes.

    Uses ``MessageBoxTimeoutW`` so an unanswered dialog closes itself; a timeout,
    No, Escape, a closed box or any error all count as a denial. The default
    button is No so a stray Enter cannot approve. A helper thread moves the box to
    the foreground so keyboard answers reach it, not the window under it.
    """
    stop = threading.Event()
    try:
        user32 = _user32()
        fn = user32.MessageBoxTimeoutW
        fn.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.UINT,
            wintypes.WORD,
            wintypes.DWORD,
        ]
        fn.restype = ctypes.c_int
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        flags = (
            MB_YESNO
            | MB_ICONWARNING
            | MB_DEFBUTTON2
            | MB_SYSTEMMODAL
            | MB_SETFOREGROUND
            | MB_TOPMOST
        )
        dialog_thread = ctypes.WinDLL("kernel32").GetCurrentThreadId()
        helper = threading.Thread(
            target=bring_dialog_forward,
            args=(
                lambda: _dialog_on_thread(user32, dialog_thread),
                lambda hwnd: _force_foreground(user32, hwnd, _send_alt_tap),
                stop,
            ),
            name="safety-confirm-focus",
            daemon=True,
        )
        helper.start()
        result = fn(None, text, title, flags, 0, int(timeout_seconds * 1000))
        return result == IDYES
    except Exception:
        logger.exception("Confirmation dialog failed; denying")
        return False
    finally:
        stop.set()


def take_screenshot(path: str) -> str | None:
    """Save a full-desktop screenshot to *path*; return the path or None on failure."""
    try:
        from PIL import ImageGrab

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        ImageGrab.grab(all_screens=True).save(path)
        return path
    except Exception:
        logger.exception("Audit screenshot failed")
        return None


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse ``ctrl+alt+shift+k`` into (modifiers, virtual-key code)."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods = 0
    for p in parts[:-1]:
        if p not in _MODIFIERS:
            raise ValueError(f"unknown modifier {p!r}")
        mods |= _MODIFIERS[p]
    key = parts[-1]
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key in ("pause", "break"):
        vk = 0x13
    elif key == "scrolllock":
        vk = 0x91
    else:
        raise ValueError(f"unsupported key {key!r}")
    if not mods:
        raise ValueError("a kill-switch hotkey needs at least one modifier")
    return mods, vk


class HotkeyListener:
    """Register a global hotkey on a dedicated message-loop thread."""

    def __init__(self, spec: str, on_press: Callable[[], None]) -> None:
        self.mods, self.vk = parse_hotkey(spec)
        self.on_press = on_press
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self.registered = threading.Event()
        self.failed = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="safety-hotkey", daemon=True)
        self._thread.start()
        self.registered.wait(2) or self.failed.wait(0)

    def _run(self) -> None:
        user32 = _user32()
        kernel32 = ctypes.WinDLL("kernel32")
        self._thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, self.mods | MOD_NOREPEAT, self.vk):
            logger.error("Kill-switch hotkey could not be registered (in use by another app?)")
            self.failed.set()
            return
        self.registered.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.on_press()
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._thread_id:
            _user32().PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


_APPX_EXE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$pkg = Get-AppxPackage | Where-Object { $_.PackageFamilyName -eq $env:WMCP_PFN } | Select-Object -First 1
if (-not $pkg) { exit 2 }
$app = (Get-AppxPackageManifest -Package $pkg).Package.Applications.Application |
    Where-Object { $_.Id -eq $env:WMCP_APPID } | Select-Object -First 1
if (-not $app -or -not $app.Executable) { exit 3 }
[Console]::Out.Write((Join-Path $pkg.InstallLocation $app.Executable))
"""


def _packaged_app_exe(aumid: str) -> str | None:
    """Executable of a packaged app from its AppUserModelID (``PFN!AppId``)."""
    import subprocess

    family, _, app_id = aumid.partition("!")
    if not family or not app_id:
        return None
    env = {**os.environ, "WMCP_PFN": family, "WMCP_APPID": app_id}
    try:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _APPX_EXE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        logger.warning("Could not query the package of %s", aumid)
        return None
    out = done.stdout.strip()
    return out if done.returncode == 0 and out else None


def read_shortcut(lnk_path: str) -> ShortcutInfo | None:
    """Target, arguments and working directory of a ``.lnk`` file, or None."""
    import win32com.client

    try:
        link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(lnk_path)
        target, arguments, working_dir = link.TargetPath, link.Arguments, link.WorkingDirectory
    except Exception:  # COM errors surface as pywintypes.com_error.
        logger.warning("Could not read shortcut %s", lnk_path)
        return None
    if not target:
        return None  # Advertised (MSI) shortcuts have no plain target.
    return ShortcutInfo(
        target=os.path.expandvars(target),
        arguments=arguments or "",
        working_dir=os.path.expandvars(working_dir) if working_dir else None,
    )


def _shortcut_target(lnk_path: str) -> str | None:
    info = read_shortcut(lnk_path)
    return info.target if info else None


def resolve_app_target(appid: str) -> str | None:
    """Executable that a Start Menu entry (path, .lnk or AppUserModelID) would run."""
    if "\\" in appid or os.path.exists(appid):
        from windows_mcp.desktop.utils import resolve_known_folder_guid_path

        path = resolve_known_folder_guid_path(appid)
        if path.lower().endswith(".lnk"):
            return _shortcut_target(path)
        return path
    if "!" in appid:
        return _packaged_app_exe(appid)
    return None  # Other AppIDs (no package, no path) cannot be mapped to an executable.


def process_exe(pid: int) -> str | None:
    import psutil

    try:
        return psutil.Process(pid).exe() or None
    except psutil.Error:
        return None


def terminate_process(pid: int) -> bool:
    import psutil

    try:
        proc = psutil.Process(pid)
        proc.terminate()
        proc.wait(timeout=5)
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False
    return True


class WindowsPlatform:
    """Bundle of the Windows calls the gate needs (replaced by a fake in tests)."""

    own_pid = os.getpid()
    process_name = staticmethod(process_name)
    foreground_pid = staticmethod(foreground_pid)
    pid_at_point = staticmethod(pid_at_point)
    pids_for_window_title = staticmethod(pids_for_window_title)
    confirm_dialog = staticmethod(confirm_dialog)
    take_screenshot = staticmethod(take_screenshot)
    hotkey_listener = HotkeyListener
    parse_hotkey = staticmethod(parse_hotkey)
    resolve_app_target = staticmethod(resolve_app_target)
    process_exe = staticmethod(process_exe)
    terminate_process = staticmethod(terminate_process)
    read_shortcut = staticmethod(read_shortcut)
