"""
uiautomation for Python 3.
Author: yinkaisheng
Source: https://github.com/yinkaisheng/Python-UIAutomation-for-Windows

This module is for UIAutomation on Windows(Windows XP with SP3, Windows Vista and Windows 7/8/8.1/10).
It supports UIAutomation for the applications which implmented IUIAutomation, such as MFC, Windows Form, WPF, Modern UI(Metro UI), Qt, Firefox and Chrome.
Run 'automation.py -h' for help.

uiautomation is shared under the Apache Licene 2.0.
This means that the code can be freely copied and distributed, and costs nothing to use.
"""

import os
import sys
import time
import shlex
import struct
import threading
import ctypes
import ctypes.wintypes
from dataclasses import dataclass
import comtypes
import comtypes.client
from _ctypes import COMError
from typing import Any, Callable, Dict, Generator, List, Tuple


METRO_WINDOW_CLASS_NAME = "Windows.UI.Core.CoreWindow"  # for Windows 8 and 8.1
SEARCH_INTERVAL = 0.5  # search control interval seconds
MAX_MOVE_SECOND = 1  # simulate mouse move or drag max seconds
TIME_OUT_SECOND = 10
OPERATION_WAIT_TIME = 0.5
MAX_PATH = 260
DEBUG_SEARCH_TIME = False
DEBUG_EXIST_DISAPPEAR = False
S_OK = 0

IsPy38OrHigher = sys.version_info[:2] >= (3, 8)
IsNT6orHigher = os.sys.getwindowsversion().major >= 6
CurrentProcessIs64Bit = sys.maxsize > 0xFFFFFFFF
ProcessTime = time.perf_counter  # this returns nearly 0 when first call it if python version <= 3.6
ProcessTime()  # need to call it once if python version <= 3.6
TreeNode = Any
from .enums import *  # noqa: E402
from .enums import _INPUTUnion  # noqa: E402
from .exceptions import from_com_error  # noqa: E402


class _AutomationClient:
    _instance = None

    @classmethod
    def instance(cls) -> "_AutomationClient":
        """Singleton instance (this prevents com creation on import)."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        try:
            ctypes.windll.ole32.CoInitialize(None)
        except Exception:
            pass
        tryCount = 3
        for retry in range(tryCount):
            try:
                self.UIAutomationCore = comtypes.client.GetModule("UIAutomationCore.dll")
                self.IUIAutomation = comtypes.client.CreateObject(
                    "{ff48dba4-60ef-4201-aa87-54103eef594e}",
                    interface=self.UIAutomationCore.IUIAutomation,
                )
                self.ViewWalker = self.IUIAutomation.RawViewWalker
                # self.ViewWalker = self.IUIAutomation.ControlViewWalker
                break
            except COMError as ex:
                if retry + 1 == tryCount:
                    raise from_com_error(ex) from ex
                time.sleep(0.1)


# set Windows dll restype
ctypes.windll.user32.GetAncestor.restype = ctypes.c_void_p
ctypes.windll.user32.GetClipboardData.restype = ctypes.c_void_p
ctypes.windll.user32.GetDC.restype = ctypes.c_void_p
ctypes.windll.user32.GetForegroundWindow.restype = ctypes.c_void_p
ctypes.windll.user32.GetWindowDC.restype = ctypes.c_void_p
ctypes.windll.user32.GetWindowLongW.restype = ctypes.wintypes.LONG
ctypes.windll.user32.OpenDesktopW.restype = ctypes.c_void_p
ctypes.windll.user32.SendMessageW.restype = ctypes.wintypes.LONG
ctypes.windll.user32.WindowFromPoint.restype = ctypes.c_void_p
ctypes.windll.gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
ctypes.windll.gdi32.SelectObject.restype = ctypes.c_void_p
ctypes.windll.kernel32.GetConsoleWindow.restype = ctypes.c_void_p
ctypes.windll.kernel32.GetStdHandle.restype = ctypes.c_void_p
ctypes.windll.kernel32.GlobalAlloc.restype = ctypes.c_void_p
ctypes.windll.kernel32.GlobalLock.restype = ctypes.c_void_p
ctypes.windll.kernel32.OpenProcess.restype = ctypes.c_void_p
ctypes.windll.ntdll.NtQueryInformationProcess.restype = ctypes.c_uint32


def _GetDictKeyName(
    theDict: Dict[str, Any],
    theValue: Any,
    keyCondition: Callable[[str], bool] | None = None,
) -> str:
    for key, value in theDict.items():
        if keyCondition:
            if keyCondition(key) and theValue == value:
                return key
        else:
            if theValue == value:
                return key
    return ""


_StdOutputHandle = -11
_ConsoleOutputHandle = ctypes.c_void_p(0)
_DefaultConsoleColor = None


def SetConsoleColor(color: int) -> bool:
    """
    Change the text color on console window.
    color: int, a value in class `ConsoleColor`.
    Return bool, True if succeed otherwise False.
    """
    global _ConsoleOutputHandle
    global _DefaultConsoleColor
    if not _DefaultConsoleColor:
        if not _ConsoleOutputHandle:
            _ConsoleOutputHandle = ctypes.c_void_p(
                ctypes.windll.kernel32.GetStdHandle(_StdOutputHandle)
            )
        bufferInfo = ConsoleScreenBufferInfo()
        ctypes.windll.kernel32.GetConsoleScreenBufferInfo(
            _ConsoleOutputHandle, ctypes.byref(bufferInfo)
        )
        _DefaultConsoleColor = int(bufferInfo.wAttributes & 0xFF)
    if sys.stdout:
        sys.stdout.flush()
    return bool(
        ctypes.windll.kernel32.SetConsoleTextAttribute(_ConsoleOutputHandle, ctypes.c_ushort(color))
    )


def ResetConsoleColor() -> bool:
    """
    Reset to the default text color on console window.
    Return bool, True if succeed otherwise False.
    """
    if sys.stdout:
        sys.stdout.flush()
    assert _DefaultConsoleColor is not None, "SetConsoleColor not previously called."
    return bool(
        ctypes.windll.kernel32.SetConsoleTextAttribute(
            _ConsoleOutputHandle, ctypes.c_ushort(_DefaultConsoleColor)
        )
    )


def WindowFromPoint(x: int, y: int) -> int:
    """
    WindowFromPoint from Win32.
    Return int, a native window handle.
    """
    return ctypes.windll.user32.WindowFromPoint(
        ctypes.wintypes.POINT(x, y)
    )  # or ctypes.windll.user32.WindowFromPoint(x, y)


def GetCursorPos() -> Tuple[int, int]:
    """
    GetCursorPos from Win32.
    Get current mouse cursor positon.
    Return Tuple[int, int], two ints tuple (x, y).
    """
    point = ctypes.wintypes.POINT(0, 0)
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def GetPhysicalCursorPos() -> Tuple[int, int]:
    """
    GetPhysicalCursorPos from Win32.
    Get current mouse cursor positon.
    Return Tuple[int, int], two ints tuple (x, y).
    """
    point = ctypes.wintypes.POINT(0, 0)
    ctypes.windll.user32.GetPhysicalCursorPos(ctypes.byref(point))
    return point.x, point.y


def SetCursorPos(x: int, y: int) -> bool:
    """
    SetCursorPos from Win32.
    Set mouse cursor to point x, y.
    x: int.
    y: int.
    Return bool, True if succeed otherwise False.
    """
    return bool(ctypes.windll.user32.SetCursorPos(x, y))


def GetDoubleClickTime() -> int:
    """
    GetDoubleClickTime from Win32.
    Return int, in milliseconds.
    """
    return ctypes.windll.user32.GetDoubleClickTime()


def mouse_event(dwFlags: int, dx: int, dy: int, dwData: int, dwExtraInfo: int) -> None:
    """mouse_event from Win32."""
    ctypes.windll.user32.mouse_event(dwFlags, dx, dy, dwData, dwExtraInfo)


def keybd_event(bVk: int, bScan: int, dwFlags: int, dwExtraInfo: int) -> None:
    """keybd_event from Win32."""
    ctypes.windll.user32.keybd_event(bVk, bScan, dwFlags, dwExtraInfo)


def PostMessage(handle: int, msg: int, wParam: int, lParam: int) -> bool:
    """
    PostMessage from Win32.
    Return bool, True if succeed otherwise False.
    """
    return bool(
        ctypes.windll.user32.PostMessageW(
            ctypes.c_void_p(handle),
            ctypes.c_uint(msg),
            ctypes.wintypes.WPARAM(wParam),
            ctypes.wintypes.LPARAM(lParam),
        )
    )


def SendMessage(handle: int, msg: int, wParam: int, lParam: int) -> int:
    """
    SendMessage from Win32.
    Return int, the return value specifies the result of the message processing;
                it depends on the message sent.
    """
    return ctypes.windll.user32.SendMessageW(
        ctypes.c_void_p(handle),
        ctypes.c_uint(msg),
        ctypes.wintypes.WPARAM(wParam),
        ctypes.wintypes.LPARAM(lParam),
    )


def Click(x: int, y: int, waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Simulate mouse click at point x, y.
    x: int.
    y: int.
    waitTime: float.
    """
    SetCursorPos(x, y)
    screenWidth, screenHeight = GetScreenSize()
    mouse_event(
        MouseEventFlag.LeftDown | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(0.05)
    mouse_event(
        MouseEventFlag.LeftUp | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(waitTime)


def MiddleClick(x: int, y: int, waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Simulate mouse middle click at point x, y.
    x: int.
    y: int.
    waitTime: float.
    """
    SetCursorPos(x, y)
    screenWidth, screenHeight = GetScreenSize()
    mouse_event(
        MouseEventFlag.MiddleDown | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(0.05)
    mouse_event(
        MouseEventFlag.MiddleUp | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(waitTime)


def RightClick(x: int, y: int, waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Simulate mouse right click at point x, y.
    x: int.
    y: int.
    waitTime: float.
    """
    SetCursorPos(x, y)
    screenWidth, screenHeight = GetScreenSize()
    mouse_event(
        MouseEventFlag.RightDown | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(0.05)
    mouse_event(
        MouseEventFlag.RightUp | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(waitTime)


def PressMouse(x: int, y: int, waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Press left mouse.
    x: int.
    y: int.
    waitTime: float.
    """
    SetCursorPos(x, y)
    screenWidth, screenHeight = GetScreenSize()
    mouse_event(
        MouseEventFlag.LeftDown | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(waitTime)


def ReleaseMouse(waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Release left mouse.
    waitTime: float.
    """
    x, y = GetCursorPos()
    screenWidth, screenHeight = GetScreenSize()
    mouse_event(
        MouseEventFlag.LeftUp | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(waitTime)


def RightPressMouse(x: int, y: int, waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Press right mouse.
    x: int.
    y: int.
    waitTime: float.
    """
    SetCursorPos(x, y)
    screenWidth, screenHeight = GetScreenSize()
    mouse_event(
        MouseEventFlag.RightDown | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(waitTime)


def RightReleaseMouse(waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Release right mouse.
    waitTime: float.
    """
    x, y = GetCursorPos()
    screenWidth, screenHeight = GetScreenSize()
    mouse_event(
        MouseEventFlag.RightUp | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(waitTime)


def MiddlePressMouse(x: int, y: int, waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Press middle mouse.
    x: int.
    y: int.
    waitTime: float.
    """
    SetCursorPos(x, y)
    screenWidth, screenHeight = GetScreenSize()
    mouse_event(
        MouseEventFlag.MiddleDown | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(waitTime)


def MiddleReleaseMouse(waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Release middle mouse.
    waitTime: float.
    """
    x, y = GetCursorPos()
    screenWidth, screenHeight = GetScreenSize()
    mouse_event(
        MouseEventFlag.MiddleUp | MouseEventFlag.Absolute,
        x * 65535 // screenWidth,
        y * 65535 // screenHeight,
        0,
        0,
    )
    time.sleep(waitTime)


def MoveTo(x: int, y: int, moveSpeed: float = 1, waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Simulate mouse move to point x, y from current cursor.
    x: int.
    y: int.
    moveSpeed: float, 1 normal speed, < 1 move slower, > 1 move faster.
    waitTime: float.
    """
    if moveSpeed <= 0:
        moveTime = 0.0
    else:
        moveTime = MAX_MOVE_SECOND / moveSpeed
    curX, curY = GetCursorPos()
    xCount = abs(x - curX)
    yCount = abs(y - curY)
    maxPoint = max(xCount, yCount)
    screenWidth, screenHeight = GetScreenSize()
    maxSide = max(screenWidth, screenHeight)
    minSide = min(screenWidth, screenHeight)
    if maxPoint > minSide:
        maxPoint = minSide
    if maxPoint < maxSide:
        maxPoint = 100 + int((maxSide - 100) / maxSide * maxPoint)
        moveTime = moveTime * maxPoint * 1.0 / maxSide
    stepCount = maxPoint // 20
    if stepCount > 1:
        xStep = (x - curX) * 1.0 / stepCount
        yStep = (y - curY) * 1.0 / stepCount
        interval = moveTime / stepCount
        for i in range(stepCount):
            cx = curX + int(xStep * i)
            cy = curY + int(yStep * i)
            # upper-left(0,0), lower-right(65536,65536)
            # mouse_event(MouseEventFlag.Move | MouseEventFlag.Absolute, cx*65536//screenWidth, cy*65536//screenHeight, 0, 0)
            SetCursorPos(cx, cy)
            time.sleep(interval)
    SetCursorPos(x, y)
    time.sleep(waitTime)


def MoveToDuration(x: int, y: int, duration: float, waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Simulate mouse move to point x, y over a bounded duration from current cursor.
    """
    curX, curY = GetCursorPos()
    if duration <= 0:
        SetCursorPos(x, y)
        time.sleep(waitTime)
        return

    stepCount = max(1, min(200, int(duration / 0.01)))
    interval = duration / stepCount
    for i in range(1, stepCount):
        ratio = i / stepCount
        cx = curX + round((x - curX) * ratio)
        cy = curY + round((y - curY) * ratio)
        SetCursorPos(cx, cy)
        time.sleep(interval)
    SetCursorPos(x, y)
    time.sleep(waitTime)


def DragDrop(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    moveSpeed: float = 1,
    waitTime: float = OPERATION_WAIT_TIME,
    duration: float | None = None,
) -> None:
    """
    Simulate mouse left button drag from point x1, y1 drop to point x2, y2.
    x1: int.
    y1: int.
    x2: int.
    y2: int.
    moveSpeed: float, 1 normal speed, < 1 move slower, > 1 move faster.
    waitTime: float.
    duration: optional seconds for bounded intermediate movement.
    """
    PressMouse(x1, y1, 0.05)
    try:
        if duration is None:
            MoveTo(x2, y2, moveSpeed, 0.05)
        else:
            MoveToDuration(x2, y2, duration, 0.05)
    except BaseException as move_error:
        try:
            ReleaseMouse(waitTime)
        except BaseException as release_error:
            raise release_error from move_error
        raise
    ReleaseMouse(waitTime)


def RightDragDrop(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    moveSpeed: float = 1,
    waitTime: float = OPERATION_WAIT_TIME,
) -> None:
    """
    Simulate mouse right button drag from point x1, y1 drop to point x2, y2.
    x1: int.
    y1: int.
    x2: int.
    y2: int.
    moveSpeed: float, 1 normal speed, < 1 move slower, > 1 move faster.
    waitTime: float.
    """
    RightPressMouse(x1, y1, 0.05)
    MoveTo(x2, y2, moveSpeed, 0.05)
    RightReleaseMouse(waitTime)


def MiddleDragDrop(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    moveSpeed: float = 1,
    waitTime: float = OPERATION_WAIT_TIME,
) -> None:
    """
    Simulate mouse middle button drag from point x1, y1 drop to point x2, y2.
    x1: int.
    y1: int.
    x2: int.
    y2: int.
    moveSpeed: float, 1 normal speed, < 1 move slower, > 1 move faster.
    waitTime: float.
    """
    MiddlePressMouse(x1, y1, 0.05)
    MoveTo(x2, y2, moveSpeed, 0.05)
    MiddleReleaseMouse(waitTime)


def WheelDown(
    wheelTimes: int = 1, interval: float = 0.05, waitTime: float = OPERATION_WAIT_TIME
) -> None:
    """
    Simulate mouse wheel down.
    wheelTimes: int.
    interval: float.
    waitTime: float.
    """
    for _i in range(wheelTimes):
        mouse_event(MouseEventFlag.Wheel, 0, 0, -120, 0)  # WHEEL_DELTA=120
        time.sleep(interval)
    time.sleep(waitTime)


def WheelUp(
    wheelTimes: int = 1, interval: float = 0.05, waitTime: float = OPERATION_WAIT_TIME
) -> None:
    """
    Simulate mouse wheel up.
    wheelTimes: int.
    interval: float.
    waitTime: float.
    """
    for _i in range(wheelTimes):
        mouse_event(MouseEventFlag.Wheel, 0, 0, 120, 0)  # WHEEL_DELTA=120
        time.sleep(interval)
    time.sleep(waitTime)


def GetScreenSize() -> Tuple[int, int]:
    """
    Return Tuple[int, int], two ints tuple (width, height).
    """
    SM_CXSCREEN = 0
    SM_CYSCREEN = 1
    w = ctypes.windll.user32.GetSystemMetrics(SM_CXSCREEN)
    h = ctypes.windll.user32.GetSystemMetrics(SM_CYSCREEN)
    return w, h


def SetScreenSize(width: int, height: int) -> bool:
    """
    Return bool.
    """
    # the size of DEVMODEW structure is too big for wrapping in ctypes,
    # so I use bytearray to simulate the structure.
    devModeSize = 220
    dmSizeOffset = 68
    dmFieldsOffset = 72
    dmPelsWidthOffset = 172
    DM_PELSWIDTH = 0x00080000
    DM_PELSHEIGHT = 0x00100000
    DISP_CHANGE_SUCCESSFUL = 0
    devMode = bytearray(devModeSize)
    devMode[dmSizeOffset : dmSizeOffset + 2] = struct.pack("<H", devModeSize)
    cDevMode = (ctypes.c_byte * devModeSize).from_buffer(devMode)
    if ctypes.windll.user32.EnumDisplaySettingsW(None, ctypes.wintypes.DWORD(-1), cDevMode):
        curWidth, curHeight = struct.unpack(
            "<II", devMode[dmPelsWidthOffset : dmPelsWidthOffset + 8]
        )
        if curWidth == width and curHeight == height:
            return True
        devMode[dmFieldsOffset : dmFieldsOffset + 4] = struct.pack(
            "<I", DM_PELSWIDTH | DM_PELSHEIGHT
        )
        devMode[dmPelsWidthOffset : dmPelsWidthOffset + 8] = struct.pack("<II", width, height)
        if ctypes.windll.user32.ChangeDisplaySettingsW(cDevMode, 0) == DISP_CHANGE_SUCCESSFUL:
            return True
    return False


def GetVirtualScreenSize() -> Tuple[int, int]:
    """
    Return Tuple[int, int], two ints tuple (width, height).
    """
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79
    w = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return w, h


def GetVirtualScreenRect() -> Tuple[int, int, int, int]:
    """Returns (left, top, width, height) of the virtual screen."""
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79
    return (
        ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


@dataclass(frozen=True)
class DisplayInfo:
    """Display identity and geometry from the Windows monitor APIs."""

    index: int
    device_name: str
    rect: "Rect"
    primary: bool


class _MonitorInfoExW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", ctypes.wintypes.RECT),
        ("rcWork", ctypes.wintypes.RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szDevice", ctypes.wintypes.WCHAR * 32),
    ]


class _DisplayDeviceW(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("DeviceName", ctypes.wintypes.WCHAR * 32),
        ("DeviceString", ctypes.wintypes.WCHAR * 128),
        ("StateFlags", ctypes.wintypes.DWORD),
        ("DeviceID", ctypes.wintypes.WCHAR * 128),
        ("DeviceKey", ctypes.wintypes.WCHAR * 128),
    ]


def _active_display_indices() -> Dict[str, int]:
    DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
    display_indices: dict[str, int] = {}
    device_index = 0
    while True:
        device = _DisplayDeviceW()
        device.cb = ctypes.sizeof(_DisplayDeviceW)
        if not ctypes.windll.user32.EnumDisplayDevicesW(
            None,
            device_index,
            ctypes.byref(device),
            0,
        ):
            break
        if device.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            display_indices[device.DeviceName.upper()] = len(display_indices)
        device_index += 1
    return display_indices


def GetDisplays() -> List[DisplayInfo]:
    """
    Get active displays keyed by zero-based active display index.
    Return List[DisplayInfo].
    """
    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.wintypes.RECT),
        ctypes.c_size_t,
    )
    displays: list[DisplayInfo] = []
    display_indices = _active_display_indices()

    def next_fallback_index() -> int:
        used_indices = set(display_indices.values()) | {display.index for display in displays}
        index = 0
        while index in used_indices:
            index += 1
        return index

    def MonitorCallback(
        hMonitor: int,
        hdcMonitor: int,
        lprcMonitor: ctypes.POINTER(ctypes.wintypes.RECT),
        dwData: int,
    ):
        info = _MonitorInfoExW()
        info.cbSize = ctypes.sizeof(_MonitorInfoExW)
        if ctypes.windll.user32.GetMonitorInfoW(ctypes.c_void_p(hMonitor), ctypes.byref(info)):
            rect = Rect(
                info.rcMonitor.left,
                info.rcMonitor.top,
                info.rcMonitor.right,
                info.rcMonitor.bottom,
            )
            device_name = info.szDevice
            index = display_indices.get(device_name.upper(), next_fallback_index())
            primary = bool(info.dwFlags & 1)
        else:
            rect = Rect(
                lprcMonitor.contents.left,
                lprcMonitor.contents.top,
                lprcMonitor.contents.right,
                lprcMonitor.contents.bottom,
            )
            index = next_fallback_index()
            device_name = f"DISPLAY{index}"
            primary = False

        displays.append(
            DisplayInfo(
                index=index,
                device_name=device_name,
                rect=rect,
                primary=primary,
            )
        )
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(
        ctypes.c_void_p(0), ctypes.c_void_p(0), MonitorEnumProc(MonitorCallback), 0
    )
    return sorted(displays, key=lambda display: display.index)


def GetMonitorsRect() -> List[Rect]:
    """
    Get monitors' rect.
    Return List[Rect].
    """
    return [display.rect for display in GetDisplays()]


def GetPixelColor(x: int, y: int, handle: int = 0) -> int:
    """
    Get pixel color of a native window.
    x: int.
    y: int.
    handle: int, the handle of a native window.
    Return int, the bgr value of point (x,y).
    r = bgr & 0x0000FF
    g = (bgr & 0x00FF00) >> 8
    b = (bgr & 0xFF0000) >> 16
    If handle is 0, get pixel from Desktop window(root control).
    Note:
    Not all devices support GetPixel.
    An application should call GetDeviceCaps to determine whether a specified device supports this function.
    For example, console window doesn't support.
    """
    hdc = ctypes.windll.user32.GetWindowDC(ctypes.c_void_p(handle))
    bgr = ctypes.windll.gdi32.GetPixel(hdc, x, y)
    ctypes.windll.user32.ReleaseDC(ctypes.c_void_p(handle), ctypes.c_void_p(hdc))
    return bgr


def MessageBox(content: str, title: str, flags: int = MB.Ok) -> int:
    """
    MessageBox from Win32.
    content: str.
    title: str.
    flags: int, a value or some combined values in class `MB`.
    Return int, a value in MB whose name starts with Id, such as MB.IdOk
    """
    return ctypes.windll.user32.MessageBoxW(
        ctypes.c_void_p(0),
        ctypes.c_wchar_p(content),
        ctypes.c_wchar_p(title),
        ctypes.c_uint(flags),
    )


def SetForegroundWindow(handle: int) -> bool:
    """
    SetForegroundWindow from Win32.
    handle: int, the handle of a native window.
    Return bool, True if succeed otherwise False.
    """
    return bool(ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(handle)))


def BringWindowToTop(handle: int) -> bool:
    """
    BringWindowToTop from Win32.
    handle: int, the handle of a native window.
    Return bool, True if succeed otherwise False.
    """
    return bool(ctypes.windll.user32.BringWindowToTop(ctypes.c_void_p(handle)))


def SwitchToThisWindow(handle: int) -> None:
    """
    SwitchToThisWindow from Win32.
    handle: int, the handle of a native window.
    """
    ctypes.windll.user32.SwitchToThisWindow(
        ctypes.c_void_p(handle), ctypes.c_int(1)
    )  # void function, no return


def GetAncestor(handle: int, flag: int) -> int:
    """
    GetAncestor from Win32.
    handle: int, the handle of a native window.
    index: int, a value in class `GAFlag`.
    Return int, a native window handle.
    """
    return ctypes.windll.user32.GetAncestor(ctypes.c_void_p(handle), ctypes.c_int(flag))


def IsTopLevelWindow(handle: int) -> bool:
    """
    IsTopLevelWindow from Win32.
    handle: int, the handle of a native window.
    Return bool.
    Only available on Windows 7 or Higher.
    """
    return bool(ctypes.windll.user32.IsTopLevelWindow(ctypes.c_void_p(handle)))


def GetWindowLong(handle: int, index: int) -> int:
    """
    GetWindowLong from Win32.
    handle: int, the handle of a native window.
    index: int.
    """
    return ctypes.windll.user32.GetWindowLongW(ctypes.c_void_p(handle), ctypes.c_int(index))


def SetWindowLong(handle: int, index: int, value: int) -> int:
    """
    SetWindowLong from Win32.
    handle: int, the handle of a native window.
    index: int.
    value: int.
    Return int, the previous value before set.
    """
    return ctypes.windll.user32.SetWindowLongW(ctypes.c_void_p(handle), index, value)


def IsIconic(handle: int) -> bool:
    """
    IsIconic from Win32.
    Determine whether a native window is minimized.
    handle: int, the handle of a native window.
    Return bool.
    """
    return bool(ctypes.windll.user32.IsIconic(ctypes.c_void_p(handle)))


def IsZoomed(handle: int) -> bool:
    """
    IsZoomed from Win32.
    Determine whether a native window is maximized.
    handle: int, the handle of a native window.
    Return bool.
    """
    return bool(ctypes.windll.user32.IsZoomed(ctypes.c_void_p(handle)))


def IsWindowVisible(handle: int) -> bool:
    """
    IsWindowVisible from Win32.
    handle: int, the handle of a native window.
    Return bool.
    """
    return bool(ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(handle)))


def ShowWindow(handle: int, cmdShow: int) -> bool:
    """
    ShowWindow from Win32.
    handle: int, the handle of a native window.
    cmdShow: int, a value in clas `SW`.
    Return bool, True if succeed otherwise False.
    """
    return bool(ctypes.windll.user32.ShowWindow(ctypes.c_void_p(handle), ctypes.c_int(cmdShow)))


def MoveWindow(handle: int, x: int, y: int, width: int, height: int, repaint: int = 1) -> bool:
    """
    MoveWindow from Win32.
    handle: int, the handle of a native window.
    x: int.
    y: int.
    width: int.
    height: int.
    repaint: int, use 1 or 0.
    Return bool, True if succeed otherwise False.
    """
    return bool(
        ctypes.windll.user32.MoveWindow(
            ctypes.c_void_p(handle),
            ctypes.c_int(x),
            ctypes.c_int(y),
            ctypes.c_int(width),
            ctypes.c_int(height),
            ctypes.c_int(repaint),
        )
    )


def SetWindowPos(
    handle: int,
    hWndInsertAfter: int,
    x: int,
    y: int,
    width: int,
    height: int,
    flags: int,
) -> bool:
    """
    SetWindowPos from Win32.
    handle: int, the handle of a native window.
    hWndInsertAfter: int, a value whose name starts with 'HWND' in class SWP.
    x: int.
    y: int.
    width: int.
    height: int.
    flags: int, values whose name starts with 'SWP' in class `SWP`.
    Return bool, True if succeed otherwise False.
    """
    return bool(
        ctypes.windll.user32.SetWindowPos(
            ctypes.c_void_p(handle),
            ctypes.c_void_p(hWndInsertAfter),
            ctypes.c_int(x),
            ctypes.c_int(y),
            ctypes.c_int(width),
            ctypes.c_int(height),
            ctypes.c_uint(flags),
        )
    )


def SetWindowTopmost(handle: int, isTopmost: bool) -> bool:
    """
    handle: int, the handle of a native window.
    isTopmost: bool
    Return bool, True if succeed otherwise False.
    """
    topValue = SWP.HWND_Topmost if isTopmost else SWP.HWND_NoTopmost
    return SetWindowPos(handle, topValue, 0, 0, 0, 0, SWP.SWP_NoSize | SWP.SWP_NoMove)


def GetWindowText(handle: int) -> str:
    """
    GetWindowText from Win32.
    handle: int, the handle of a native window.
    Return str.
    """
    arrayType = ctypes.c_wchar * MAX_PATH
    values = arrayType()
    ctypes.windll.user32.GetWindowTextW(ctypes.c_void_p(handle), values, ctypes.c_int(MAX_PATH))
    return values.value


def SetWindowText(handle: int, text: str) -> bool:
    """
    SetWindowText from Win32.
    handle: int, the handle of a native window.
    text: str.
    Return bool, True if succeed otherwise False.
    """
    return bool(
        ctypes.windll.user32.SetWindowTextW(ctypes.c_void_p(handle), ctypes.c_wchar_p(text))
    )


def GetEditText(handle: int) -> str:
    """
    Get text of a native Win32 Edit.
    handle: int, the handle of a native window.
    Return str.
    """
    textLen = SendMessage(handle, 0x000E, 0, 0) + 1  # WM_GETTEXTLENGTH
    arrayType = ctypes.c_wchar * textLen
    values = arrayType()
    SendMessage(handle, 0x000D, textLen, ctypes.addressof(values))  # WM_GETTEXT
    return values.value


def GetConsoleOriginalTitle() -> str:
    """
    GetConsoleOriginalTitle from Win32.
    Return str.
    Only available on Windows Vista or higher.
    """
    if IsNT6orHigher:
        arrayType = ctypes.c_wchar * MAX_PATH
        values = arrayType()
        ctypes.windll.kernel32.GetConsoleOriginalTitleW(values, ctypes.c_uint(MAX_PATH))
        return values.value
    else:
        raise RuntimeError("GetConsoleOriginalTitle is not supported on Windows XP or lower.")


def GetConsoleTitle() -> str:
    """
    GetConsoleTitle from Win32.
    Return str.
    """
    arrayType = ctypes.c_wchar * MAX_PATH
    values = arrayType()
    ctypes.windll.kernel32.GetConsoleTitleW(values, ctypes.c_uint(MAX_PATH))
    return values.value


def SetConsoleTitle(text: str) -> bool:
    """
    SetConsoleTitle from Win32.
    text: str.
    Return bool, True if succeed otherwise False.
    """
    return bool(ctypes.windll.kernel32.SetConsoleTitleW(ctypes.c_wchar_p(text)))


def GetForegroundWindow() -> int:
    """
    GetForegroundWindow from Win32.
    Return int, the native handle of the foreground window.
    """
    return ctypes.windll.user32.GetForegroundWindow()


def DwmIsCompositionEnabled() -> bool:
    """
    DwmIsCompositionEnabled from dwmapi.
    Return bool.
    """
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
        dwmapi.DwmIsCompositionEnabled.restype = ctypes.HRESULT
        isEnabled = ctypes.wintypes.BOOL()
        hr = dwmapi.DwmIsCompositionEnabled(ctypes.byref(isEnabled))
        if hr == S_OK:
            return bool(isEnabled.value)
        else:
            return False
    except Exception:
        return False


def DwmGetWindowExtendFrameBounds(handle: int) -> Rect | None:
    """
    Get Native Window Rect without invisible resize borders.
    Return Rect or None. If handle is not top level, return None.
    """
    try:
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        dwmapi = ctypes.WinDLL("dwmapi")
        dwmapi.DwmGetWindowAttribute.restype = ctypes.HRESULT
        rect = ctypes.wintypes.RECT()
        hr = dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(handle),
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(ctypes.wintypes.RECT),
        )
        if hr == S_OK:
            return Rect(rect.left, rect.top, rect.right, rect.bottom)
        return None
    except Exception:
        return None


def GetWindowRect(handle: int) -> Rect | None:
    """
    GetWindowRect from user32.
    Return RECT.
    """
    user32 = ctypes.windll.user32
    rect = ctypes.wintypes.RECT()
    success = user32.GetWindowRect(ctypes.c_void_p(handle), ctypes.byref(rect))
    if success:
        return Rect(rect.left, rect.top, rect.right, rect.bottom)
    return None


def IsDesktopLocked() -> bool:
    """
    Check if desktop is locked.
    Return bool.
    Desktop is locked if press Win+L, Ctrl+Alt+Del or in remote desktop mode.
    """
    isLocked = False
    desk = ctypes.windll.user32.OpenDesktopW(
        ctypes.c_wchar_p("Default"),
        ctypes.c_uint(0),
        ctypes.c_int(0),
        ctypes.c_uint(0x0100),
    )  # DESKTOP_SWITCHDESKTOP = 0x0100
    if desk:
        isLocked = not ctypes.windll.user32.SwitchDesktop(ctypes.c_void_p(desk))
        ctypes.windll.user32.CloseDesktop(ctypes.c_void_p(desk))
    return isLocked


def PlayWaveFile(
    filePath: str = r"C:\Windows\Media\notify.wav",
    isAsync: bool = False,
    isLoop: bool = False,
) -> bool:
    """
    Call PlaySound from Win32.
    filePath: str, if emtpy, stop playing the current sound.
    isAsync: bool, if True, the sound is played asynchronously and returns immediately.
    isLoop: bool, if True, the sound plays repeatedly until PlayWaveFile(None) is called again, must also set isAsync to True.
    Return bool, True if succeed otherwise False.
    """
    if filePath:
        SND_ASYNC = 0x0001
        SND_NODEFAULT = 0x0002
        SND_LOOP = 0x0008
        SND_FILENAME = 0x20000
        flags = SND_NODEFAULT | SND_FILENAME
        if isAsync:
            flags |= SND_ASYNC
        if isLoop:
            flags |= SND_LOOP
            flags |= SND_ASYNC
        return bool(
            ctypes.windll.winmm.PlaySoundW(
                ctypes.c_wchar_p(filePath), ctypes.c_void_p(0), ctypes.c_uint(flags)
            )
        )
    else:
        return bool(
            ctypes.windll.winmm.PlaySoundW(
                ctypes.c_wchar_p(0), ctypes.c_void_p(0), ctypes.c_uint(0)
            )
        )


def IsProcess64Bit(processId: int) -> bool | None:
    """
    Return True if process is 64 bit.
    Return False if process is 32 bit.
    Return None if unknown, maybe caused by having no access right to the process.
    """
    try:
        IsWow64Process = ctypes.windll.kernel32.IsWow64Process
    except Exception:
        return False
    hProcess = ctypes.windll.kernel32.OpenProcess(
        0x1000, 0, processId
    )  # PROCESS_QUERY_INFORMATION=0x0400,PROCESS_QUERY_LIMITED_INFORMATION=0x1000
    if hProcess:
        hProcess = ctypes.c_void_p(hProcess)
        isWow64 = ctypes.wintypes.BOOL()
        if IsWow64Process(hProcess, ctypes.byref(isWow64)):
            ctypes.windll.kernel32.CloseHandle(hProcess)
            return not isWow64
        else:
            ctypes.windll.kernel32.CloseHandle(hProcess)
    return None


def IsUserAnAdmin() -> bool:
    """
    IsUserAnAdmin from Win32.
    Return bool.
    Minimum supported OS: Windows XP, Windows Server 2003
    """
    return bool(ctypes.windll.shell32.IsUserAnAdmin()) if IsNT6orHigher else True


def RunScriptAsAdmin(
    argv: List[str], workingDirectory: str = None, showFlag: int = SW.ShowNormal
) -> bool:
    """
    Run a python script as administrator.
    System will show a popup dialog askes you whether to elevate as administrator if UAC is enabled.
    argv: List[str], a str list like sys.argv, argv[0] is the script file, argv[1:] are other arguments.
    workingDirectory: str, the working directory for the script file.
    showFlag: int, a value in class `SW`.
    Return bool, True if succeed.
    """
    args = shlex.join(argv)
    return (
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, args, workingDirectory, showFlag
        )
        > 32
    )


def SendKey(key: int, waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Simulate typing a key.
    key: int, a value in class `Keys`.
    """
    keybd_event(key, 0, KeyboardEventFlag.KeyDown | KeyboardEventFlag.ExtendedKey, 0)
    keybd_event(key, 0, KeyboardEventFlag.KeyUp | KeyboardEventFlag.ExtendedKey, 0)
    time.sleep(waitTime)


def PressKey(key: int, waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Simulate a key down for key.
    key: int, a value in class `Keys`.
    waitTime: float.
    """
    keybd_event(key, 0, KeyboardEventFlag.KeyDown | KeyboardEventFlag.ExtendedKey, 0)
    time.sleep(waitTime)


def ReleaseKey(key: int, waitTime: float = OPERATION_WAIT_TIME) -> None:
    """
    Simulate a key up for key.
    key: int, a value in class `Keys`.
    waitTime: float.
    """
    keybd_event(key, 0, KeyboardEventFlag.KeyUp | KeyboardEventFlag.ExtendedKey, 0)
    time.sleep(waitTime)


def IsKeyPressed(key: int) -> bool:
    """
    key: int, a value in class `Keys`.
    Return bool.
    """
    state = ctypes.windll.user32.GetAsyncKeyState(key)
    return bool(state & 0x8000)


def _CreateInput(structure) -> INPUT:
    """
    Create Win32 struct `INPUT` for `SendInput`.
    Return `INPUT`.
    """
    if isinstance(structure, MOUSEINPUT):
        return INPUT(InputType.Mouse, _INPUTUnion(mi=structure))
    if isinstance(structure, KEYBDINPUT):
        return INPUT(InputType.Keyboard, _INPUTUnion(ki=structure))
    if isinstance(structure, HARDWAREINPUT):
        return INPUT(InputType.Hardware, _INPUTUnion(hi=structure))
    raise TypeError("Cannot create INPUT structure!")


def MouseInput(
    dx: int,
    dy: int,
    mouseData: int = 0,
    dwFlags: int = MouseEventFlag.LeftDown,
    time_: int = 0,
) -> INPUT:
    """
    Create Win32 struct `MOUSEINPUT` for `SendInput`.
    Return `INPUT`.
    """
    return _CreateInput(MOUSEINPUT(dx, dy, mouseData, dwFlags, time_, None))


def KeyboardInput(
    wVk: int, wScan: int, dwFlags: int = KeyboardEventFlag.KeyDown, time_: int = 0
) -> INPUT:
    """Create Win32 struct `KEYBDINPUT` for `SendInput`."""
    return _CreateInput(KEYBDINPUT(wVk, wScan, dwFlags, time_, None))


def HardwareInput(uMsg: int, param: int = 0) -> INPUT:
    """Create Win32 struct `HARDWAREINPUT` for `SendInput`."""
    return _CreateInput(HARDWAREINPUT(uMsg, param & 0xFFFF, param >> 16 & 0xFFFF))


def SendInput(*inputs) -> int:
    """
    SendInput from Win32.
    input: `INPUT`.
    Return int, the number of events that it successfully inserted into the keyboard or mouse input stream.
                If the function returns zero, the input was already blocked by another thread.
    """
    cbSize = ctypes.c_int(ctypes.sizeof(INPUT))
    for ip in inputs:
        ret = ctypes.windll.user32.SendInput(1, ctypes.byref(ip), cbSize)
    return ret
    # or one call
    # nInputs = len(inputs)
    # LPINPUT = INPUT * nInputs
    # pInputs = LPINPUT(*inputs)
    # cbSize = ctypes.c_int(ctypes.sizeof(INPUT))
    # return ctypes.windll.user32.SendInput(nInputs, ctypes.byref(pInputs), cbSize)


def SendUnicodeChar(char: str, charMode: bool = True) -> int:
    """
    Type a single unicode char.
    char: str, len(char) must equal to 1.
    charMode: bool, if False, the char typied is depend on the input method if a input method is on.
    Return int, the number of events that it successfully inserted into the keyboard or mouse input stream.
                If the function returns zero, the input was already blocked by another thread.
    """
    if charMode:
        vk = 0
        scan = ord(char)
        flag = KeyboardEventFlag.KeyUnicode
    else:
        res = ctypes.windll.user32.VkKeyScanW(ctypes.wintypes.WCHAR(char))
        if (res >> 8) & 0xFF == 0:
            vk = res & 0xFF
            scan = 0
            flag = 0
        else:
            vk = 0
            scan = ord(char)
            flag = KeyboardEventFlag.KeyUnicode
    return SendInput(
        KeyboardInput(vk, scan, flag | KeyboardEventFlag.KeyDown),
        KeyboardInput(vk, scan, flag | KeyboardEventFlag.KeyUp),
    )


_SCKeys = {
    Keys.VK_LSHIFT: 0x02A,
    Keys.VK_RSHIFT: 0x136,
    Keys.VK_LCONTROL: 0x01D,
    Keys.VK_RCONTROL: 0x11D,
    Keys.VK_LMENU: 0x038,
    Keys.VK_RMENU: 0x138,
    Keys.VK_LWIN: 0x15B,
    Keys.VK_RWIN: 0x15C,
    Keys.VK_NUMPAD0: 0x52,
    Keys.VK_NUMPAD1: 0x4F,
    Keys.VK_NUMPAD2: 0x50,
    Keys.VK_NUMPAD3: 0x51,
    Keys.VK_NUMPAD4: 0x4B,
    Keys.VK_NUMPAD5: 0x4C,
    Keys.VK_NUMPAD6: 0x4D,
    Keys.VK_NUMPAD7: 0x47,
    Keys.VK_NUMPAD8: 0x48,
    Keys.VK_NUMPAD9: 0x49,
    Keys.VK_DECIMAL: 0x53,
    Keys.VK_NUMLOCK: 0x145,
    Keys.VK_DIVIDE: 0x135,
    Keys.VK_MULTIPLY: 0x037,
    Keys.VK_SUBTRACT: 0x04A,
    Keys.VK_ADD: 0x04E,
}


def _VKtoSC(key: int) -> int:
    """
    This function is only for internal use in SendKeys.
    key: int, a value in class `Keys`.
    Return int.
    """
    if key in _SCKeys:
        return _SCKeys[key]
    scanCode = ctypes.windll.user32.MapVirtualKeyA(key, 0)
    if not scanCode:
        return 0
    keyList = [
        Keys.VK_APPS,
        Keys.VK_CANCEL,
        Keys.VK_SNAPSHOT,
        Keys.VK_DIVIDE,
        Keys.VK_NUMLOCK,
    ]
    if key in keyList:
        scanCode |= 0x0100
    return scanCode


def SendKeys(
    text: str,
    interval: float = 0.01,
    waitTime: float = OPERATION_WAIT_TIME,
    charMode: bool = True,
    debug: bool = False,
) -> None:
    """
    Simulate typing keys on keyboard.
    text: str, keys to type.
    interval: float, seconds between keys.
    waitTime: float.
    charMode: bool, if False, the text typed is depend on the input method if a input method is on.
    debug: bool, if True, print the keys.
    Examples:
    {Ctrl}, {Delete} ... are special keys' name in SpecialKeyNames.
    SendKeys('{Ctrl}a{Delete}{Ctrl}v{Ctrl}s{Ctrl}{Shift}s{Win}e{PageDown}') #press Ctrl+a, Delete, Ctrl+v, Ctrl+s, Ctrl+Shift+s, Win+e, PageDown
    SendKeys('{Ctrl}(AB)({Shift}(123))') #press Ctrl+A+B, type '(', press Shift+1+2+3, type ')', if '()' follows a hold key, hold key won't release until ')'
    SendKeys('{Ctrl}{a 3}') #press Ctrl+a at the same time, release Ctrl+a, then type 'a' 2 times
    SendKeys('{a 3}{B 5}') #type 'a' 3 times, type 'B' 5 times
    SendKeys('{{}Hello{}}abc {a}{b}{c} test{} 3}{!}{a} (){(}{)}') #type: '{Hello}abc abc test}}}!a ()()'
    SendKeys('0123456789{Enter}')
    SendKeys('ABCDEFGHIJKLMNOPQRSTUVWXYZ{Enter}')
    SendKeys('abcdefghijklmnopqrstuvwxyz{Enter}')
    SendKeys('`~!@#$%^&*()-_=+{Enter}')
    SendKeys('[]{{}{}}\\|;:\'\",<.>/?{Enter}')
    """
    holdKeys = (
        "WIN",
        "LWIN",
        "RWIN",
        "SHIFT",
        "LSHIFT",
        "RSHIFT",
        "CTRL",
        "CONTROL",
        "LCTRL",
        "RCTRL",
        "LCONTROL",
        "LCONTROL",
        "ALT",
        "LALT",
        "RALT",
    )
    keys = []
    printKeys = []
    i = 0
    insertIndex = 0
    length = len(text)
    hold = False
    include = False
    lastKeyValue = None
    while True:
        if text[i] == "{":
            rindex = text.find("}", i)
            if rindex == i + 1:  # {}}
                rindex = text.find("}", i + 2)
            if rindex == -1:
                raise ValueError('"{" or "{}" is not valid, use "{{}" for "{", use "{}}" for "}"')
            keyStr = text[i + 1 : rindex]
            key = [it for it in keyStr.split(" ") if it]
            if not key:
                raise ValueError(
                    '"{}" is not valid, use "{{Space}}" or " " for " "'.format(text[i : rindex + 1])
                )
            if (len(key) == 2 and not key[1].isdigit()) or len(key) > 2:
                raise ValueError('"{}" is not valid'.format(text[i : rindex + 1]))
            upperKey = key[0].upper()
            count = 1
            if len(key) > 1:
                count = int(key[1])
            for _j in range(count):
                if hold:
                    if upperKey in SpecialKeyNames:
                        keyValue = SpecialKeyNames[upperKey]
                        if type(lastKeyValue) is type(keyValue) and lastKeyValue == keyValue:
                            insertIndex += 1
                        printKeys.insert(insertIndex, (key[0], "KeyDown | ExtendedKey"))
                        printKeys.insert(insertIndex + 1, (key[0], "KeyUp | ExtendedKey"))
                        keys.insert(
                            insertIndex,
                            (
                                keyValue,
                                KeyboardEventFlag.KeyDown | KeyboardEventFlag.ExtendedKey,
                            ),
                        )
                        keys.insert(
                            insertIndex + 1,
                            (
                                keyValue,
                                KeyboardEventFlag.KeyUp | KeyboardEventFlag.ExtendedKey,
                            ),
                        )
                        lastKeyValue = keyValue
                    elif key[0] in CharacterCodes:
                        keyValue = CharacterCodes[key[0]]
                        if type(lastKeyValue) is type(keyValue) and lastKeyValue == keyValue:
                            insertIndex += 1
                        printKeys.insert(insertIndex, (key[0], "KeyDown | ExtendedKey"))
                        printKeys.insert(insertIndex + 1, (key[0], "KeyUp | ExtendedKey"))
                        keys.insert(
                            insertIndex,
                            (
                                keyValue,
                                KeyboardEventFlag.KeyDown | KeyboardEventFlag.ExtendedKey,
                            ),
                        )
                        keys.insert(
                            insertIndex + 1,
                            (
                                keyValue,
                                KeyboardEventFlag.KeyUp | KeyboardEventFlag.ExtendedKey,
                            ),
                        )
                        lastKeyValue = keyValue
                    else:
                        printKeys.insert(insertIndex, (key[0], "UnicodeChar"))
                        keys.insert(insertIndex, (key[0], "UnicodeChar"))
                        lastKeyValue = key[0]
                    if include:
                        insertIndex += 1
                    else:
                        if upperKey in holdKeys:
                            insertIndex += 1
                        else:
                            hold = False
                else:
                    if upperKey in SpecialKeyNames:
                        keyValue = SpecialKeyNames[upperKey]
                        printKeys.append((key[0], "KeyDown | ExtendedKey"))
                        printKeys.append((key[0], "KeyUp | ExtendedKey"))
                        keys.append(
                            (
                                keyValue,
                                KeyboardEventFlag.KeyDown | KeyboardEventFlag.ExtendedKey,
                            )
                        )
                        keys.append(
                            (
                                keyValue,
                                KeyboardEventFlag.KeyUp | KeyboardEventFlag.ExtendedKey,
                            )
                        )
                        lastKeyValue = keyValue
                        if upperKey in holdKeys:
                            hold = True
                            insertIndex = len(keys) - 1
                        else:
                            hold = False
                    else:
                        printKeys.append((key[0], "UnicodeChar"))
                        keys.append((key[0], "UnicodeChar"))
                        lastKeyValue = key[0]
            i = rindex + 1
        elif text[i] == "(":
            if hold:
                include = True
            else:
                printKeys.append((text[i], "UnicodeChar"))
                keys.append((text[i], "UnicodeChar"))
                lastKeyValue = text[i]
            i += 1
        elif text[i] == ")":
            if hold:
                include = False
                hold = False
            else:
                printKeys.append((text[i], "UnicodeChar"))
                keys.append((text[i], "UnicodeChar"))
                lastKeyValue = text[i]
            i += 1
        else:
            if hold:
                if text[i] in CharacterCodes:
                    keyValue = CharacterCodes[text[i]]
                    if (
                        include
                        and type(lastKeyValue) is type(keyValue)
                        and lastKeyValue == keyValue
                    ):
                        insertIndex += 1
                    printKeys.insert(insertIndex, (text[i], "KeyDown | ExtendedKey"))
                    printKeys.insert(insertIndex + 1, (text[i], "KeyUp | ExtendedKey"))
                    keys.insert(
                        insertIndex,
                        (
                            keyValue,
                            KeyboardEventFlag.KeyDown | KeyboardEventFlag.ExtendedKey,
                        ),
                    )
                    keys.insert(
                        insertIndex + 1,
                        (
                            keyValue,
                            KeyboardEventFlag.KeyUp | KeyboardEventFlag.ExtendedKey,
                        ),
                    )
                    lastKeyValue = keyValue
                else:
                    printKeys.append((text[i], "UnicodeChar"))
                    keys.append((text[i], "UnicodeChar"))
                    lastKeyValue = text[i]
                if include:
                    insertIndex += 1
                else:
                    hold = False
            else:
                printKeys.append((text[i], "UnicodeChar"))
                keys.append((text[i], "UnicodeChar"))
                lastKeyValue = text[i]
            i += 1
        if i >= length:
            break
    hotkeyInterval = 0.01
    for i, key in enumerate(keys):
        if key[1] == "UnicodeChar":
            SendUnicodeChar(key[0], charMode)
            time.sleep(interval)
            if debug:
                pass
        else:
            scanCode = _VKtoSC(key[0])
            keybd_event(key[0], scanCode, key[1], 0)
            if debug:
                pass
            if i + 1 == len(keys):
                time.sleep(interval)
                if debug:
                    pass
            else:
                if key[1] & KeyboardEventFlag.KeyUp:
                    if (
                        keys[i + 1][1] == "UnicodeChar"
                        or keys[i + 1][1] & KeyboardEventFlag.KeyUp == 0
                    ):
                        time.sleep(interval)
                        if debug:
                            pass
                    else:
                        time.sleep(
                            hotkeyInterval
                        )  # must sleep for a while, otherwise combined keys may not be caught
                        if debug:
                            pass
                else:  # KeyboardEventFlag.KeyDown
                    time.sleep(hotkeyInterval)
                    if debug:
                        pass
    # make sure hold keys are not pressed
    # win = ctypes.windll.user32.GetAsyncKeyState(Keys.VK_LWIN)
    # ctrl = ctypes.windll.user32.GetAsyncKeyState(Keys.VK_CONTROL)
    # alt = ctypes.windll.user32.GetAsyncKeyState(Keys.VK_MENU)
    # shift = ctypes.windll.user32.GetAsyncKeyState(Keys.VK_SHIFT)
    # if shift & 0x8000:
    # keybd_event(Keys.VK_SHIFT, 0, KeyboardEventFlag.KeyUp | KeyboardEventFlag.ExtendedKey, 0)
    time.sleep(waitTime)


def SetThreadDpiAwarenessContext(dpiAwarenessContext: int):
    """
    SetThreadDpiAwarenessContext from Win32.
    dpiAwarenessContext: int, a value in class `DpiAwarenessContext`
    """
    try:
        # https://docs.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpiawarenesscontext
        # Windows 10 1607+
        ctypes.windll.user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        oldContext = ctypes.windll.user32.SetThreadDpiAwarenessContext(
            ctypes.c_void_p(dpiAwarenessContext)
        )
        return oldContext
    except Exception:
        pass


def SetProcessDpiAwareness(dpiAwareness: int):
    """
    Set process DPI awareness so UIA coordinates (BoundingRectangle, Click, MoveTo, etc.)
    use physical pixels consistently. Required for correct behavior on scaled displays.

    dpiAwareness: int, a value in class `ProcessDpiAwareness`
    """
    try:
        # https://docs.microsoft.com/en-us/windows/win32/api/shellscalingapi/nf-shellscalingapi-setprocessdpiawareness
        # Once set, any future calls will fail. Windows 8.1+
        return ctypes.windll.shcore.SetProcessDpiAwareness(dpiAwareness)
    except Exception:
        try:
            # Fallback for Windows 7 / older: system DPI aware (no per-monitor)
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# Ensure DPI-aware coordinates at module load (before any UIA calls)
SetProcessDpiAwareness(ProcessDpiAwareness.PerMonitorDpiAware)


class tagPROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.wintypes.DWORD),
        ("cntUsage", ctypes.wintypes.DWORD),
        ("th32ProcessID", ctypes.wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.wintypes.ULONG)),
        ("th32ModuleID", ctypes.wintypes.DWORD),
        ("cntThreads", ctypes.wintypes.DWORD),
        ("th32ParentProcessID", ctypes.wintypes.DWORD),
        ("pcPriClassBase", ctypes.wintypes.LONG),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


class ProcessInfo:
    def __init__(
        self,
        exeName: str,
        pid: int,
        ppid: int = -1,
        exePath: str = "",
        cmdLine: str = "",
    ):
        self.pid = pid
        self.ppid = ppid  # ppid is -1 if failed
        self.exeName = exeName  # such as explorer.exe
        self.is64Bit = None  # True if is 64 bit, False if 32 bit, None if failed
        self.exePath = exePath  # such as C:\Windows\explorer.exe, empty if failed
        self.cmdLine = cmdLine  # empty if failed

    def __str__(self):
        return "ProcessInfo(pid={}, ppid={}, exeName='{}', is64Bit={}, exePath='{}', cmdLine='{}'".format(
            self.pid, self.ppid, self.exeName, self.is64Bit, self.exePath, self.cmdLine
        )

    def __repr__(self):
        return "<{} object at 0x{:08X} {}>".format(
            self.__class__.__name__,
            id(self),
            ", ".join("{}={}".format(k, v) for k, v in self.__dict__.items()),
        )


def GetProcesses(detailedInfo: bool = True) -> List[ProcessInfo]:
    """
    Enum process by Win32 API.
    detailedInfo: bool, only get pid and exeName if False.
    You should run python as administrator to call this function.
    Can not get some system processes' info.
    """
    if detailedInfo:
        try:
            IsWow64Process = ctypes.windll.kernel32.IsWow64Process
        except Exception:
            IsWow64Process = None
    hSnapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(15, 0)  # TH32CS_SNAPALL = 15
    processEntry32 = tagPROCESSENTRY32()
    processEntry32.dwSize = ctypes.sizeof(processEntry32)
    processList = []
    processNext = ctypes.windll.kernel32.Process32FirstW(
        ctypes.c_void_p(hSnapshot), ctypes.byref(processEntry32)
    )
    cPointerSize = ctypes.sizeof(ctypes.c_void_p)
    while processNext:
        pinfo = ProcessInfo(processEntry32.szExeFile, processEntry32.th32ProcessID)
        if detailedInfo:
            # PROCESS_QUERY_INFORMATION=0x0400, PROCESS_QUERY_LIMITED_INFORMATION=0x1000, PROCESS_VM_READ=0x0010
            queryType = (0x1000 if IsNT6orHigher else 0x0400) | 0x0010
            hProcess = ctypes.windll.kernel32.OpenProcess(queryType, 0, pinfo.pid)
            if hProcess:
                hProcess = ctypes.c_void_p(hProcess)
                processBasicInformationAddr = 0
                processBasicInformation = (
                    ctypes.c_size_t * 6
                )()  # sizeof PROCESS_BASIC_INFORMATION
                outLen = ctypes.c_ulong(0)
                ctypes.windll.ntdll.NtQueryInformationProcess.restype = ctypes.c_uint32
                if IsWow64Process:
                    isWow64 = ctypes.wintypes.BOOL()
                    if IsWow64Process(hProcess, ctypes.byref(isWow64)):
                        pinfo.is64Bit = not isWow64
                else:
                    pinfo.is64Bit = False
                ntStatus = ctypes.windll.ntdll.NtQueryInformationProcess(
                    hProcess,
                    processBasicInformationAddr,
                    processBasicInformation,
                    ctypes.sizeof(processBasicInformation),
                    ctypes.byref(outLen),
                )
                if ntStatus == 0:  # STATUS_SUCCESS=0
                    pinfo.ppid = processBasicInformation[5]
                    pebBaseAddress = processBasicInformation[1]
                    if pebBaseAddress:
                        pebSize = 712 if CurrentProcessIs64Bit else 472  # sizeof PEB
                        peb = (ctypes.c_size_t * (pebSize // cPointerSize))()
                        outLen.value = 0
                        isok = ctypes.windll.kernel32.ReadProcessMemory(
                            hProcess,
                            ctypes.c_void_p(pebBaseAddress),
                            peb,
                            pebSize,
                            ctypes.byref(outLen),
                        )
                        if isok:
                            processParametersAddr = ctypes.c_void_p(peb[4])
                            uppSize = (
                                128 if CurrentProcessIs64Bit else 72
                            )  # sizeof RTL_USER_PROCESS_PARAMETERS
                            upp = (ctypes.c_ubyte * uppSize)()
                            outLen.value = 0
                            isok = ctypes.windll.kernel32.ReadProcessMemory(
                                hProcess,
                                processParametersAddr,
                                upp,
                                uppSize,
                                ctypes.byref(outLen),
                            )
                            if isok:
                                offset = 16 + 10 * cPointerSize
                                (
                                    imgPathSize,
                                    imgPathSizeMax,
                                    imgPathAddr,
                                    cmdLineSize,
                                    cmdLineSizeMax,
                                    cmdLineAddr,
                                ) = struct.unpack("@HHNHHN", bytes(upp[offset:]))
                                exePath = (ctypes.c_wchar * imgPathSizeMax)()
                                outLen.value = 0
                                isok = ctypes.windll.kernel32.ReadProcessMemory(
                                    hProcess,
                                    ctypes.c_void_p(imgPathAddr),
                                    exePath,
                                    ctypes.sizeof(exePath),
                                    ctypes.byref(outLen),
                                )
                                if isok:
                                    pinfo.exePath = exePath.value
                                cmdLine = (ctypes.c_wchar * cmdLineSizeMax)()
                                outLen.value = 0
                                isok = ctypes.windll.kernel32.ReadProcessMemory(
                                    hProcess,
                                    ctypes.c_void_p(cmdLineAddr),
                                    cmdLine,
                                    ctypes.sizeof(cmdLine),
                                    ctypes.byref(outLen),
                                )
                                if isok:
                                    pinfo.cmdLine = cmdLine.value
                if not pinfo.exePath:
                    exePath = (ctypes.c_wchar * MAX_PATH)()
                    if IsNT6orHigher:
                        win32PathFormat = 0  # nativeSystemPathFormat = 1
                        outLen.value = len(exePath)
                        isok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                            hProcess, win32PathFormat, exePath, ctypes.byref(outLen)
                        )
                    else:
                        hModule = None
                        try:
                            # strlen =
                            ctypes.windll.psapi.GetModuleFileNameExW(
                                hProcess, hModule, exePath, len(exePath)
                            )
                        except Exception:
                            # strlen =
                            ctypes.windll.kernel32.GetModuleFileNameExW(
                                hProcess, hModule, exePath, len(exePath)
                            )
                        # exePath is nativeSystemPathFormat
                        # strlen = ctypes.windll.psapi.GetProcessImageFileNameW(hProcess, exePath, len(exePath))
                        # if exePath.value:
                        # strlen = ctypes.windll.kernel32.QueryDosDeviceW(ctypes.c_wchar_p(exePath.value), exePath, len(exePath))
                    pinfo.exePath = exePath.value
                ctypes.windll.kernel32.CloseHandle(hProcess)
        processList.append(pinfo)
        processNext = ctypes.windll.kernel32.Process32NextW(
            ctypes.c_void_p(hSnapshot), ctypes.byref(processEntry32)
        )
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(hSnapshot))
    return processList


def EnumProcessByWMI() -> Generator[ProcessInfo, None, None]:
    """Maybe slower, but can get system processes' info"""
    import wmi  # pip install wmi

    wobj = wmi.WMI()
    fields = ["Name", "ProcessId", "ParentProcessId", "ExecutablePath", "CommandLine"]
    for it in wobj.Win32_Process(fields):  # only query the specified fields, speed up the process
        pinfo = ProcessInfo(
            it.Name, it.ProcessId, it.ParentProcessId, it.ExecutablePath, it.CommandLine
        )
        yield pinfo


def TerminateProcess(pid: int) -> bool:
    hProcess = ctypes.windll.kernel32.OpenProcess(0x0001, 0, pid)  # PROCESS_TERMINATE=0x0001
    if hProcess:
        hProcess = ctypes.c_void_p(hProcess)
        ret = ctypes.windll.kernel32.TerminateProcess(hProcess, -1)
        ctypes.windll.kernel32.CloseHandle(hProcess)
        return bool(ret)
    return False


def TerminateProcessByName(exeName: str, killAll: bool = True) -> int:
    """
    exeName: str, such as notepad.exe
    return int, process count that was terminated
    """
    count = 0
    for pinfo in GetProcesses(detailedInfo=False):
        if pinfo.exeName == exeName:
            if TerminateProcess(pinfo.pid):
                count += 1
                if not killAll:
                    break
    return count


_ClipboardLock = threading.Lock()


def _OpenClipboard(value):
    end = ProcessTime() + 0.2
    while ProcessTime() < end:
        ret = ctypes.windll.user32.OpenClipboard(value)
        if ret:
            return ret
        time.sleep(0.005)


def GetClipboardFormats() -> Dict[int, str]:
    """
    Get clipboard formats that system clipboard has currently.
    Return Dict[int, str].
    The key is a int value in class `ClipboardFormat` or othes values that apps registered by ctypes.windll.user32.RegisterClipboardFormatW
    """
    formats = {}
    with _ClipboardLock:
        if _OpenClipboard(0):
            formatType = 0
            arrayType = ctypes.c_wchar * 64
            while True:
                formatType = ctypes.windll.user32.EnumClipboardFormats(formatType)
                if formatType == 0:
                    break
                values = arrayType()
                ctypes.windll.user32.GetClipboardFormatNameW(formatType, values, len(values))
                formatName = values.value
                if not formatName:
                    formatName = _GetDictKeyName(
                        ClipboardFormat.__dict__,
                        formatType,
                        lambda key: key.startswith("CF_"),
                    )
                formats[formatType] = formatName
            ctypes.windll.user32.CloseClipboard()
    return formats


def GetClipboardText() -> str:
    with _ClipboardLock:
        if _OpenClipboard(0):
            if ctypes.windll.user32.IsClipboardFormatAvailable(ClipboardFormat.CF_UNICODETEXT):
                hClipboardData = ctypes.windll.user32.GetClipboardData(
                    ClipboardFormat.CF_UNICODETEXT
                )
                hText = ctypes.windll.kernel32.GlobalLock(ctypes.c_void_p(hClipboardData))
                text = ctypes.c_wchar_p(hText).value
                ctypes.windll.kernel32.GlobalUnlock(ctypes.c_void_p(hClipboardData))
                ctypes.windll.user32.CloseClipboard()
                if text is None:
                    return ""
                return text
    return ""


def SetClipboardText(text: str) -> bool:
    """
    Return bool, True if succeed otherwise False.
    """
    ret = False
    with _ClipboardLock:
        if _OpenClipboard(0):
            ctypes.windll.user32.EmptyClipboard()
            textByteLen = (len(text) + 1) * 2
            hClipboardData = ctypes.windll.kernel32.GlobalAlloc(0x2, textByteLen)  # GMEM_MOVEABLE
            hDestText = ctypes.windll.kernel32.GlobalLock(ctypes.c_void_p(hClipboardData))
            ctypes.cdll.msvcrt.wcsncpy(
                ctypes.c_wchar_p(hDestText),
                ctypes.c_wchar_p(text),
                ctypes.c_size_t(textByteLen // 2),
            )
            ctypes.windll.kernel32.GlobalUnlock(ctypes.c_void_p(hClipboardData))
            # system owns hClipboardData after calling SetClipboardData,
            # application can not write to or free the data once ownership has been transferred to the system
            if ctypes.windll.user32.SetClipboardData(
                ctypes.c_uint(ClipboardFormat.CF_UNICODETEXT),
                ctypes.c_void_p(hClipboardData),
            ):
                ret = True
            else:
                ctypes.windll.kernel32.GlobalFree(ctypes.c_void_p(hClipboardData))
            ctypes.windll.user32.CloseClipboard()
    return ret


def GetClipboardHtml() -> str:
    """
    Return str.
    Note: the positions(StartHTML, EndHTML ...) are valid for utf-8 encoding html text,
        when the utf-8 encoding html text is decoded to Python unicode str,
        the positions may not correspond to the actual positions in the returned str.
    """
    with _ClipboardLock:
        if _OpenClipboard(0):
            if ctypes.windll.user32.IsClipboardFormatAvailable(ClipboardFormat.CF_HTML):
                hClipboardData = ctypes.windll.user32.GetClipboardData(ClipboardFormat.CF_HTML)
                hText = ctypes.windll.kernel32.GlobalLock(ctypes.c_void_p(hClipboardData))
                v = ctypes.c_char_p(hText).value
                ctypes.windll.kernel32.GlobalUnlock(ctypes.c_void_p(hClipboardData))
                ctypes.windll.user32.CloseClipboard()
                if v is None:
                    return ""
                return v.decode("utf-8")
    return ""


def SetClipboardHtml(htmlText: str) -> bool:
    """
    htmlText: str, such as '<h1>Title</h1><h3>Hello</h3><p>hello world</p>'
    Return bool, True if succeed otherwise False.
    Refer: https://docs.microsoft.com/en-us/troubleshoot/cpp/add-html-code-clipboard
    """
    u8Html = htmlText.encode("utf-8")
    formatBytes = b"Version:0.9\r\nStartHTML:00000000\r\nEndHTML:00000000\r\nStartFragment:00000000\r\nEndFragment:00000000\r\n<html>\r\n<body>\r\n<!--StartFragment-->{}<!--EndFragment-->\r\n</body>\r\n</html>"
    startHtml = formatBytes.find(b"<html>")
    endHtml = len(formatBytes) + len(u8Html) - 2
    startFragment = formatBytes.find(b"{}")
    endFragment = formatBytes.find(b"<!--EndFragment-->") + len(u8Html) - 2
    formatBytes = formatBytes.replace(
        b"StartHTML:00000000", "StartHTML:{:08}".format(startHtml).encode("utf-8")
    )
    formatBytes = formatBytes.replace(
        b"EndHTML:00000000", "EndHTML:{:08}".format(endHtml).encode("utf-8")
    )
    formatBytes = formatBytes.replace(
        b"StartFragment:00000000",
        "StartFragment:{:08}".format(startFragment).encode("utf-8"),
    )
    formatBytes = formatBytes.replace(
        b"EndFragment:00000000", "EndFragment:{:08}".format(endFragment).encode("utf-8")
    )
    u8Result = formatBytes.replace(b"{}", u8Html)
    ret = False
    with _ClipboardLock:
        if _OpenClipboard(0):
            ctypes.windll.user32.EmptyClipboard()
            hClipboardData = ctypes.windll.kernel32.GlobalAlloc(
                0x2002, len(u8Result) + 4
            )  # GMEM_MOVEABLE |GMEM_DDESHARE
            hDestText = ctypes.windll.kernel32.GlobalLock(ctypes.c_void_p(hClipboardData))
            ctypes.cdll.msvcrt.strncpy(
                ctypes.c_char_p(hDestText), ctypes.c_char_p(u8Result), len(u8Result)
            )
            ctypes.windll.kernel32.GlobalUnlock(ctypes.c_void_p(hClipboardData))
            # system owns hClipboardData after calling SetClipboardData,
            # application can not write to or free the data once ownership has been transferred to the system
            if ctypes.windll.user32.SetClipboardData(
                ctypes.c_uint(ClipboardFormat.CF_HTML), ctypes.c_void_p(hClipboardData)
            ):
                ret = True
            else:
                ctypes.windll.kernel32.GlobalFree(ctypes.c_void_p(hClipboardData))
            ctypes.windll.user32.CloseClipboard()
    return ret


def Input(prompt: str, consoleColor: int = ConsoleColor.Default) -> str:
    return input()


def InputColorfully(prompt: str, consoleColor: int = ConsoleColor.Default) -> str:
    return input()


class Rect:
    """
    class Rect, like `ctypes.wintypes.RECT`.
    """

    def __init__(self, left: int = 0, top: int = 0, right: int = 0, bottom: int = 0):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def width(self) -> int:
        return self.right - self.left

    def height(self) -> int:
        return self.bottom - self.top

    def xcenter(self) -> int:
        return self.left + self.width() // 2

    def ycenter(self) -> int:
        return self.top + self.height() // 2

    def isempty(self) -> int:
        return self.width() == 0 or self.height() == 0

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def intersect(self, rect: "Rect") -> "Rect":
        left, top, right, bottom = (
            max(self.left, rect.left),
            max(self.top, rect.top),
            min(self.right, rect.right),
            min(self.bottom, rect.bottom),
        )
        return Rect(left, top, right, bottom)

    def offset(self, x: int, y: int) -> None:
        self.left += x
        self.right += x
        self.top += y
        self.bottom += y

    def __eq__(self, rect):
        return (
            self.left == rect.left
            and self.top == rect.top
            and self.right == rect.right
            and self.bottom == rect.bottom
        )

    def __str__(self) -> str:
        return "({},{},{},{})[{}x{}]".format(
            self.left, self.top, self.right, self.bottom, self.width(), self.height()
        )

    def __repr__(self) -> str:
        return "{}({},{},{},{})[{}x{}]".format(
            self.__class__.__name__,
            self.left,
            self.top,
            self.right,
            self.bottom,
            self.width(),
            self.height(),
        )


class ClipboardFormat:
    CF_TEXT = 1
    CF_BITMAP = 2
    CF_METAFILEPICT = 3
    CF_SYLK = 4
    CF_DIF = 5
    CF_TIFF = 6
    CF_OEMTEXT = 7
    CF_DIB = 8
    CF_PALETTE = 9
    CF_PENDATA = 10
    CF_RIFF = 11
    CF_WAVE = 12
    CF_UNICODETEXT = 13
    CF_ENHMETAFILE = 14
    CF_HDROP = 15
    CF_LOCALE = 16
    CF_DIBV5 = 17
    CF_MAX = 18
    CF_HTML = ctypes.windll.user32.RegisterClipboardFormatW("HTML Format")


class ExtendedProperty(ctypes.Structure):
    _fields_ = [
        ("PropertyName", ctypes.c_wchar_p),
        ("PropertyValue", ctypes.c_wchar_p),
    ]


class UIAutomationEventInfo(ctypes.Structure):
    _fields_ = [
        ("guid", ctypes.c_void_p),
        ("pProgrammaticName", ctypes.wintypes.LPCWSTR),
    ]


class UIAutomationMethodInfo(ctypes.Structure):
    _fields_ = [
        ("pProgrammaticName", ctypes.wintypes.LPCWSTR),
        ("doSetFocus", ctypes.wintypes.BOOL),
        ("cInParameters", ctypes.wintypes.UINT),
        ("cOutParameters", ctypes.wintypes.UINT),
        ("pParameterTypes", ctypes.c_void_p),
        ("pParameterNames", ctypes.c_void_p),
    ]


class UIAutomationParameter(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_void_p),
        ("pData", ctypes.c_void_p),
    ]


class UIAutomationPatternInfo(ctypes.Structure):
    _fields_ = [
        ("guid", ctypes.c_void_p),
        ("pProgrammaticName", ctypes.wintypes.LPCWSTR),
        ("providerInterfaceId", ctypes.c_void_p),
        ("clientInterfaceId", ctypes.c_void_p),
        ("cProperties", ctypes.wintypes.UINT),
        ("UIAutomationPropertyInfo", ctypes.c_void_p),
        ("cMethods", ctypes.wintypes.UINT),
        ("UIAutomationMethodInfo", ctypes.c_void_p),
        ("cEvents", ctypes.wintypes.UINT),
        ("UIAutomationEventInfo", ctypes.c_void_p),
        ("pPatternHandler", ctypes.c_void_p),
    ]


class UIAutomationPropertyInfo(ctypes.Structure):
    _fields_ = [
        ("guid", ctypes.c_void_p),
        ("pProgrammaticName", ctypes.wintypes.LPCWSTR),
        ("type", ctypes.c_void_p),
    ]


class UiaAndOrCondition(ctypes.Structure):
    _fields_ = [
        ("ConditionType", ctypes.c_void_p),
        ("ppConditions", ctypes.c_void_p),
        ("UiaCondition", ctypes.c_void_p),
        ("cConditions", ctypes.c_int),
    ]


class UiaAsyncContentLoadedEventArgs(ctypes.Structure):
    _fields_ = [
        ("Type", ctypes.c_void_p),
        ("EventId", ctypes.c_int),
        ("AsyncContentLoadedState", ctypes.c_void_p),
        ("PercentComplete", ctypes.c_double),
    ]


class UiaCacheRequest(ctypes.Structure):
    _fields_ = [
        ("UiaCondition", ctypes.c_void_p),
        ("Scope", ctypes.c_int),
        ("pProperties", ctypes.c_void_p),
        ("cProperties", ctypes.c_int),
        ("pPatterns", ctypes.c_void_p),
        ("cPatterns", ctypes.c_int),
        ("automationElementMode", ctypes.c_int),
    ]


class UiaChangeInfo(ctypes.Structure):
    _fields_ = [
        ("uiaId", ctypes.c_int),
        ("payload", ctypes.c_void_p),
        ("extraInfo", ctypes.c_void_p),
    ]


class UiaCondition(ctypes.Structure):
    _fields_ = [
        ("ConditionType", ctypes.c_void_p),
    ]


class UiaEventArgs(ctypes.Structure):
    _fields_ = [
        ("Type", ctypes.c_void_p),
        ("EventId", ctypes.c_int),
    ]


class UiaFindParams(ctypes.Structure):
    _fields_ = [
        ("MaxDepth", ctypes.c_int),
        ("FindFirst", ctypes.wintypes.BOOL),
        ("ExcludeRoot", ctypes.wintypes.BOOL),
        ("UiaCondition", ctypes.c_void_p),
    ]


class UiaNotCondition(ctypes.Structure):
    _fields_ = [
        ("ConditionType", ctypes.c_void_p),
        ("UiaCondition", ctypes.c_void_p),
    ]


class UiaPoint(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_double),
        ("y", ctypes.c_double),
    ]


class UiaPropertyChangedEventArgs(ctypes.Structure):
    _fields_ = [
        ("Type", ctypes.c_void_p),
        ("EventId", ctypes.c_int),
        ("PropertyId", ctypes.c_int),
        ("OldValue", ctypes.c_void_p),
        ("NewValue", ctypes.c_void_p),
    ]


class UiaPropertyCondition(ctypes.Structure):
    _fields_ = [
        ("ConditionType", ctypes.c_void_p),
        ("PropertyId", ctypes.c_int),
        ("Value", ctypes.c_void_p),
        ("Flags", ctypes.c_void_p),
    ]


class UiaRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_double),
        ("top", ctypes.c_double),
        ("width", ctypes.c_double),
        ("height", ctypes.c_double),
    ]


class UiaStructureChangedEventArgs(ctypes.Structure):
    _fields_ = [
        ("Type", ctypes.c_void_p),
        ("EventId", ctypes.c_int),
        ("StructureChangeType", ctypes.c_void_p),
        ("pRuntimeId", ctypes.c_void_p),
        ("cRuntimeIdLen", ctypes.c_int),
    ]


class UiaWindowClosedEventArgs(ctypes.Structure):
    _fields_ = [
        ("Type", ctypes.c_void_p),
        ("EventId", ctypes.c_int),
        ("pRuntimeId", ctypes.c_void_p),
        ("cRuntimeIdLen", ctypes.c_int),
    ]


class CacheRequest:
    """
    Wrapper for IUIAutomationCacheRequest.
    """

    def __init__(self, cache_request=None):
        if cache_request:
            self.check_request = cache_request
        else:
            self.check_request = _AutomationClient.instance().IUIAutomation.CreateCacheRequest()

    @property
    def TreeScope(self) -> int:
        return self.check_request.TreeScope

    @TreeScope.setter
    def TreeScope(self, scope: int):
        self.check_request.TreeScope = scope

    @property
    def AutomationElementMode(self) -> int:
        return self.check_request.AutomationElementMode

    @AutomationElementMode.setter
    def AutomationElementMode(self, mode: int):
        self.check_request.AutomationElementMode = mode

    @property
    def TreeFilter(self):
        return self.check_request.TreeFilter

    @TreeFilter.setter
    def TreeFilter(self, filter):
        self.check_request.TreeFilter = filter

    def AddProperty(self, propertyId: int):
        """
        Adds a property to the cache request.
        propertyId: int, PropertyId.
        """
        self.check_request.AddProperty(propertyId)

    def AddPattern(self, patternId: int):
        """
        Adds a pattern to the cache request.
        patternId: int, PatternId.
        """
        self.check_request.AddPattern(patternId)

    def Clone(self) -> "CacheRequest":
        """
        Clones the cache request.
        """
        cloned = self.check_request.Clone()
        return CacheRequest(cloned)


def CreateCacheRequest() -> CacheRequest:
    """
    Creates a new CacheRequest.
    """
    return CacheRequest()


# Event Handling Implementations for core.py


def AddAutomationEventHandler(eventId: int, element, scope: int, cacheRequest, handler) -> None:
    """
    Registers a method that handles Microsoft UI Automation events.
    """
    _AutomationClient.instance().IUIAutomation.AddAutomationEventHandler(
        eventId, element, scope, cacheRequest, handler
    )


def RemoveAutomationEventHandler(eventId: int, element, handler) -> None:
    """
    Removes the specified Microsoft UI Automation event handler.
    """
    _AutomationClient.instance().IUIAutomation.RemoveAutomationEventHandler(
        eventId, element, handler
    )


def AddPropertyChangedEventHandler(
    element, scope: int, cacheRequest, handler, propertyArray: List[int]
) -> None:
    """
    Registers a method that handles UI Automation property-changed events.
    """
    # Convert propertyArray to a ctypes array if needed, but comtypes usually handles lists
    # However, AddPropertyChangedEventHandler expects a pointer to an array of property IDs
    # Let's see how generic we can be.
    # The signature in IUIAutomation is: HRESULT AddPropertyChangedEventHandler(ptr_element, scope, ptr_cacheRequest, ptr_handler, ptr_propertyArray)
    # The last arg is SAFEARRAY(int)

    # We might need to manually convert list to SAFEARRAY or rely on comtypes.
    # For now, let's pass a tuple/list and see if comtypes marshals it.
    _AutomationClient.instance().IUIAutomation.AddPropertyChangedEventHandler(
        element, scope, cacheRequest, handler, propertyArray
    )


def RemovePropertyChangedEventHandler(element, handler) -> None:
    """
    Removes the specified property-changed event handler.
    """
    _AutomationClient.instance().IUIAutomation.RemovePropertyChangedEventHandler(element, handler)


def AddStructureChangedEventHandler(element, scope: int, cacheRequest, handler) -> None:
    """
    Registers a method that handles UI Automation structure-changed events.
    """
    _AutomationClient.instance().IUIAutomation.AddStructureChangedEventHandler(
        element, scope, cacheRequest, handler
    )


def RemoveStructureChangedEventHandler(element, handler) -> None:
    """
    Removes the specified structure-changed event handler.
    """
    _AutomationClient.instance().IUIAutomation.RemoveStructureChangedEventHandler(element, handler)


def AddFocusChangedEventHandler(cacheRequest, handler) -> None:
    """
    Registers a method that handles UI Automation focus-changed events.
    """
    _AutomationClient.instance().IUIAutomation.AddFocusChangedEventHandler(cacheRequest, handler)


def RemoveFocusChangedEventHandler(handler) -> None:
    """
    Removes the specified focus-changed event handler.
    """
    _AutomationClient.instance().IUIAutomation.RemoveFocusChangedEventHandler(handler)


def RemoveAllEventHandlers() -> None:
    """
    Removes all registered Microsoft UI Automation event handlers.
    """
    _AutomationClient.instance().IUIAutomation.RemoveAllEventHandlers()


# Condition creation helper functions


def CreateTrueCondition():
    """
    Create a condition that is always true. This matches all elements.

    Return: A condition object that can be used with FindAll, FindFirst, etc.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-createtruecondition
    """
    return _AutomationClient.instance().IUIAutomation.CreateTrueCondition()


def CreateFalseCondition():
    """
    Create a condition that is always false. This matches no elements.

    Return: A condition object that can be used with FindAll, FindFirst, etc.
    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-createfalsecondition
    """
    return _AutomationClient.instance().IUIAutomation.CreateFalseCondition()


def CreatePropertyCondition(propertyId: int, value):
    """
    Create a condition that matches elements with a specific property value.

    propertyId: int, a value in class `PropertyId`.
    value: The value to match for the property.
    Return: A condition object that can be used with FindAll, FindFirst, etc.

    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-createpropertycondition
    """
    return _AutomationClient.instance().IUIAutomation.CreatePropertyCondition(propertyId, value)


def CreateAndCondition(condition1, condition2):
    """
    Create a condition that is the logical AND of two conditions.

    condition1: First condition.
    condition2: Second condition.
    Return: A condition object that can be used with FindAll, FindFirst, etc.

    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-createandcondition
    """
    return _AutomationClient.instance().IUIAutomation.CreateAndCondition(condition1, condition2)


def CreateOrCondition(condition1, condition2):
    """
    Create a condition that is the logical OR of two conditions.

    condition1: First condition.
    condition2: Second condition.
    Return: A condition object that can be used with FindAll, FindFirst, etc.

    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-createorcondition
    """
    return _AutomationClient.instance().IUIAutomation.CreateOrCondition(condition1, condition2)


def CreateNotCondition(condition):
    """
    Create a condition that is the logical NOT of another condition.

    condition: The condition to negate.
    Return: A condition object that can be used with FindAll, FindFirst, etc.

    Refer https://docs.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-createnotcondition
    """
    return _AutomationClient.instance().IUIAutomation.CreateNotCondition(condition)
