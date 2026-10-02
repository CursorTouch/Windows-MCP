"""Win32 structures, API bindings and desktop health for input ownership."""

import ctypes
import queue
import threading
import time
from ctypes import wintypes
from typing import Any

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
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
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
    try:
        if not atom:
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
            owner._thread_beat = time.monotonic()
            while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
            for _ in range(128):
                try:
                    event = owner._events.get_nowait()
                except queue.Empty:
                    break
                owner._handle(event)
            owner._handle(("tick",))
            if owner._rotate or time.monotonic() - last_rotate >= 5.0:
                owner._rotating = True  # Pause AI without letting physical down leak.
                try:
                    owner._install_hooks(instance)
                    owner._rotate = False
                    last_rotate = time.monotonic()
                    owner._recover_after_rehook()
                finally:
                    owner._rotating = False
            time.sleep(0.01)
    except Exception as exc:
        owner._startup_error = exc
        owner._fail_open()
        owner._ready.set()
    finally:
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
