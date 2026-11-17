"""Win32 structures, API bindings and desktop health for input ownership."""

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Any

from windows_mcp.desktop.control_recovery import StaleInputRecovery

_LRESULT = ctypes.c_ssize_t
_HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_WNDPROC = ctypes.WINFUNCTYPE(
    _LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class _WndClass(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class _MouseHookData(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KeyHookData(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _RawDevice(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class _RawHeader(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RawMouse(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.USHORT),
        ("ulRawButtons", wintypes.DWORD),
        ("lLastX", ctypes.c_long),
        ("lLastY", ctypes.c_long),
        ("ulExtraInformation", wintypes.DWORD),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = _LRESULT
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.RegisterClassExW.argtypes = [ctypes.POINTER(_WndClass)]
_user32.RegisterClassExW.restype = ctypes.c_ushort
_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HANDLE,
    wintypes.HINSTANCE,
    ctypes.c_void_p,
]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
_user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_user32.OpenInputDesktop.restype = wintypes.HANDLE
_user32.CloseDesktop.argtypes = [wintypes.HANDLE]
_user32.GetUserObjectInformationW.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
_user32.GetUserObjectInformationW.restype = wintypes.BOOL
_user32.RegisterRawInputDevices.argtypes = [
    ctypes.POINTER(_RawDevice),
    wintypes.UINT,
    wintypes.UINT,
]
_user32.GetRawInputData.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
]
_user32.GetRawInputData.restype = wintypes.UINT
_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.DefWindowProcW.restype = _LRESULT
_user32.MsgWaitForMultipleObjectsEx.argtypes = [
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
]
_user32.MsgWaitForMultipleObjectsEx.restype = wintypes.DWORD
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.SetEvent.argtypes = [wintypes.HANDLE]
_kernel32.SetEvent.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
_wts.WTSQuerySessionInformationW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.DWORD),
]
_wts.WTSQuerySessionInformationW.restype = wintypes.BOOL
_wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]

_CHORD = {0x10, 0x11, 0x12, 0x08}

# The hook pump must block instead of polling: low-level hooks are delivered on
# the thread that installed them, so every millisecond spent idling there is
# charged in full to the next SendInput caller (and to physical input).
_QS_ALLINPUT = 0x04FF  # Keyboard, mouse, raw input, posted messages and timers.
_MWMO_INPUTAVAILABLE = 0x0004  # Wake on input already queued, not just new input.
_MWMO_ALERTABLE = 0x0002  # Same alertability GetMessage gives a normal UI pump.
# Windows silently drops a low-level hook that times out; reinstall on this period.
_ROTATE_SECONDS = 5.0
# While a physical hold is tracked, wake often enough for StaleInputRecovery's
# spaced samples. Otherwise nothing on this thread is time-driven except rotation:
# input, posted messages and the wake event (faults, stop, liveness pings) end the wait.
_RECOVERY_WAIT_MS = 100


def _pump_timeout_ms(owner: Any, now: float, last_rotate: float) -> int:
    """How long the idle pump may block before it has timed work to do."""
    rotate_in = max(0, int(round((last_rotate + _ROTATE_SECONDS - now) * 1000)))
    if owner._pressed or owner._mouse_down:
        return min(rotate_in, _RECOVERY_WAIT_MS)
    return rotate_in


def wake_pump(handle: Any) -> None:
    """Wake a pump parked in its message wait; safe from any thread, never blocks."""
    if handle:
        _kernel32.SetEvent(handle)


def _key(vk: int) -> int:
    if vk in (0xA0, 0xA1):
        return 0x10
    if vk in (0xA2, 0xA3):
        return 0x11
    if vk in (0xA4, 0xA5):
        return 0x12
    return vk


def _mouse_button(message: int, data: int) -> tuple[int, bool] | None:
    down = {0x201: 1, 0x204: 2, 0x207: 3}
    up = {0x202: 1, 0x205: 2, 0x208: 3}
    if message in down:
        return down[message], True
    if message in up:
        return up[message], False
    if message in (0x20B, 0x20C):  # WM_XBUTTONDOWN / WM_XBUTTONUP.
        xbutton = (data >> 16) & 0xFFFF
        if xbutton in (1, 2):
            return xbutton + 3, message == 0x20B
    return None


def _read_raw_mouse(lparam: int) -> tuple[int, int, int] | None:
    """Read a relative physical mouse event; null devices are ambiguous."""
    size = wintypes.UINT()
    header_size = ctypes.sizeof(_RawHeader)
    if (
        _user32.GetRawInputData(lparam, 0x10000003, None, ctypes.byref(size), header_size)
        == 0xFFFFFFFF
    ):
        return None
    if size.value < header_size + ctypes.sizeof(_RawMouse):
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if (
        _user32.GetRawInputData(lparam, 0x10000003, buffer, ctypes.byref(size), header_size)
        == 0xFFFFFFFF
    ):
        return None
    header = ctypes.cast(buffer, ctypes.POINTER(_RawHeader)).contents
    if header.dwType != 0 or not header.hDevice:
        return None
    mouse = _RawMouse.from_buffer_copy(buffer, header_size)
    if mouse.usFlags & 1:
        return None
    return int(header.hDevice), mouse.lLastX, mouse.lLastY


def _interactive_desktop() -> bool:
    """Require an active session on the normal user input desktop."""
    buffer = ctypes.c_void_p()
    length = wintypes.DWORD()
    # WTS_CURRENT_SESSION, WTSConnectState=8; WTSActive=0.
    if not _wts.WTSQuerySessionInformationW(
        None, 0xFFFFFFFF, 8, ctypes.byref(buffer), ctypes.byref(length)
    ):
        return False
    try:
        if length.value < ctypes.sizeof(ctypes.c_int):
            return False
        if ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int)).contents.value != 0:
            return False
    finally:
        _wts.WTSFreeMemory(buffer)
    # The input desktop changes to Winlogon for UAC/lock screen.
    desktop = _user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS.
    if not desktop:
        return False
    try:
        name = ctypes.create_unicode_buffer(64)
        needed = wintypes.DWORD()
        return (
            bool(
                _user32.GetUserObjectInformationW(
                    desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)
                )
            )
            and name.value.casefold() == "default"
        )
    finally:
        _user32.CloseDesktop(desktop)


def run_input_monitor(owner: Any) -> None:
    """Pump the Win32 hooks and raw input for one control coordinator."""
    recovery = StaleInputRecovery()  # Never carry confirmations across a restart.
    name = f"WindowsMCPControl{threading.get_ident()}"
    instance = _kernel32.GetModuleHandleW(None)
    owner._mouse_callback = _HOOKPROC(owner._physical_mouse)
    owner._key_callback = _HOOKPROC(owner._physical_key)

    @_WNDPROC
    def window_proc(hwnd, message, wparam, lparam):
        if message == 0x00FF:  # WM_INPUT
            try:
                owner._raw_input(lparam)
            except Exception:
                owner._fail_open()
        return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

    owner._window_callback = window_proc
    wc = _WndClass()
    wc.cbSize = ctypes.sizeof(wc)
    wc.lpfnWndProc = window_proc
    wc.hInstance = instance
    wc.lpszClassName = name
    atom = _user32.RegisterClassExW(ctypes.byref(wc))
    hwnd = None
    registered = False
    # Auto-reset: one SetEvent from any thread ends exactly one wait.
    wake = wintypes.HANDLE(_kernel32.CreateEventW(None, False, False, None))
    owner._pump_event = wake.value
    try:
        if not atom:
            raise ctypes.WinError(ctypes.get_last_error())
        if not wake.value:
            raise ctypes.WinError(ctypes.get_last_error())
        hwnd = _user32.CreateWindowExW(0, name, name, 0, 0, 0, 0, 0, None, None, instance, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        device = _RawDevice(1, 2, 0x100, hwnd)  # Mouse, RIDEV_INPUTSINK.
        if not _user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
            raise ctypes.WinError(ctypes.get_last_error())
        registered = True
        owner._install_hooks(instance)
        owner._ready.set()
        last_rotate = time.monotonic()
        msg = wintypes.MSG()
        while not owner._stop.is_set():
            # Beat before clearing idle: the worker reads idle first, then the beat.
            owner._thread_beat = time.monotonic()
            owner._pump_idle = False
            while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
            # Only this thread mutates physical sets. Never wait for business
            # locks here: a sender may hold them while Windows calls our hooks.
            recovery.sample(owner, time.monotonic(), lambda vk: _user32.GetAsyncKeyState(vk))
            if owner._rotate or time.monotonic() - last_rotate >= _ROTATE_SECONDS:
                owner._rotating = True  # Pause AI without letting physical down leak.
                try:
                    # Clear before installation so a new fault can request another.
                    owner._rotate = False
                    fault = owner._fault_token
                    owner._install_hooks(instance)
                    last_rotate = time.monotonic()
                    owner._rehook_completed = fault
                    owner._release_requested.set()
                finally:
                    owner._rotating = False
            # Block until there is something to pump instead of sleeping past it.
            # A low-level hook is delivered on this thread, so an unconditional
            # sleep here is a fixed tax on the latency of every injected and
            # physical event: it wakes only when input actually arrives, when
            # another thread signals the wake event, or when timed work is due.
            timeout = _pump_timeout_ms(owner, time.monotonic(), last_rotate)
            owner._pump_idle = True  # Liveness is now proven by answering a ping.
            _user32.MsgWaitForMultipleObjectsEx(
                1,
                ctypes.byref(wake),
                timeout,
                _QS_ALLINPUT,
                _MWMO_INPUTAVAILABLE | _MWMO_ALERTABLE,
            )
    except Exception as exc:
        owner._startup_error = exc
        owner._fail_open()
        owner._ready.set()
    finally:
        owner._pump_idle = False
        owner._suppress = False
        for hook in (owner._mouse_hook, owner._key_hook):
            if hook:
                _user32.UnhookWindowsHookEx(hook)
        owner._mouse_hook = owner._key_hook = None
        if registered:
            removed = _RawDevice(1, 2, 1, None)  # RIDEV_REMOVE.
            _user32.RegisterRawInputDevices(ctypes.byref(removed), 1, ctypes.sizeof(removed))
        if hwnd:
            _user32.DestroyWindow(hwnd)
        if atom:
            _user32.UnregisterClassW(name, instance)
        owner._pump_event = None
        if wake.value:
            _kernel32.CloseHandle(wake)
