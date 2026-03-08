from windows_mcp.desktop.utils import ps_quote, ps_quote_for_xml, approximate_color_name
import pathlib
from windows_mcp.vdm.core import (
    get_all_desktops,
    get_current_desktop,
    is_window_on_current_desktop,
)
from windows_mcp.desktop.views import DesktopState, Window, Browser, Status, Size
from windows_mcp.tree.views import BoundingBox, TreeElementNode
from concurrent.futures import ThreadPoolExecutor
from PIL import ImageGrab, ImageFont, ImageDraw, Image
from windows_mcp.tree.service import Tree
from locale import getpreferredencoding
from contextlib import contextmanager
from typing import Literal
from markdownify import markdownify
from fuzzywuzzy import process
from time import sleep, time
from psutil import Process
import win32process
import subprocess
import win32gui
import win32con
import requests
import logging
import base64
import random
import ctypes
import shutil
import csv
import re
import os
import io
import tempfile

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

import windows_mcp.uia as uia  # noqa: E402

# Key name aliases for shortcut keys that differ from UIA SpecialKeyNames
_KEY_ALIASES = {
    "backspace": "Back",
    "capslock": "Capital",
    "scrolllock": "Scroll",
    "windows": "Win",
    "command": "Win",
    "option": "Alt",
}

# Virtual key code mapping for KeyHold tool
_VK_MAP = {
    "shift": uia.Keys.VK_SHIFT,
    "ctrl": uia.Keys.VK_CONTROL,
    "control": uia.Keys.VK_CONTROL,
    "alt": uia.Keys.VK_MENU,
    "win": uia.Keys.VK_LWIN,
    "windows": uia.Keys.VK_LWIN,
    "enter": uia.Keys.VK_RETURN,
    "return": uia.Keys.VK_RETURN,
    "tab": uia.Keys.VK_TAB,
    "escape": uia.Keys.VK_ESCAPE,
    "esc": uia.Keys.VK_ESCAPE,
    "space": uia.Keys.VK_SPACE,
    "backspace": uia.Keys.VK_BACK,
    "delete": uia.Keys.VK_DELETE,
    "insert": uia.Keys.VK_INSERT,
    "home": uia.Keys.VK_HOME,
    "end": uia.Keys.VK_END,
    "pageup": uia.Keys.VK_PRIOR,
    "pagedown": uia.Keys.VK_NEXT,
    "up": uia.Keys.VK_UP,
    "down": uia.Keys.VK_DOWN,
    "left": uia.Keys.VK_LEFT,
    "right": uia.Keys.VK_RIGHT,
    "f1": uia.Keys.VK_F1,
    "f2": uia.Keys.VK_F2,
    "f3": uia.Keys.VK_F3,
    "f4": uia.Keys.VK_F4,
    "f5": uia.Keys.VK_F5,
    "f6": uia.Keys.VK_F6,
    "f7": uia.Keys.VK_F7,
    "f8": uia.Keys.VK_F8,
    "f9": uia.Keys.VK_F9,
    "f10": uia.Keys.VK_F10,
    "f11": uia.Keys.VK_F11,
    "f12": uia.Keys.VK_F12,
    "capslock": uia.Keys.VK_CAPITAL,
    "numlock": uia.Keys.VK_NUMLOCK,
    "scrolllock": uia.Keys.VK_SCROLL,
    "printscreen": uia.Keys.VK_SNAPSHOT,
}

# BGR color values for Win32 GDI highlight rendering
_HIGHLIGHT_COLORS = {
    "red": 0x0000FF,
    "green": 0x00FF00,
    "blue": 0xFF0000,
    "yellow": 0x00FFFF,
}


def _escape_text_for_sendkeys(text: str) -> str:
    """Escape special characters so uia.SendKeys types them correctly."""
    result = []
    for ch in text:
        if ch == "{":
            result.append("{{}")
        elif ch == "}":
            result.append("{}}")
        elif ch == "\n":
            result.append("{Enter}")
        elif ch == "\t":
            result.append("{Tab}")
        elif ch == "\r":
            continue
        else:
            result.append(ch)
    return "".join(result)


class Desktop:
    def __init__(self):
        self.encoding = getpreferredencoding()
        self.tree = Tree(self)
        self.desktop_state = None

    def get_state(
        self,
        use_annotation: bool | str = True,
        use_vision: bool | str = False,
        use_dom: bool | str = False,
        as_bytes: bool | str = False,
        scale: float = 1.0,
    ) -> DesktopState:
        use_annotation = use_annotation is True or (
            isinstance(use_annotation, str) and use_annotation.lower() == "true"
        )
        use_vision = use_vision is True or (
            isinstance(use_vision, str) and use_vision.lower() == "true"
        )
        use_dom = use_dom is True or (isinstance(use_dom, str) and use_dom.lower() == "true")
        as_bytes = as_bytes is True or (isinstance(as_bytes, str) and as_bytes.lower() == "true")

        start_time = time()

        controls_handles = self.get_controls_handles()  # Taskbar,Program Manager,Apps, Dialogs
        windows, windows_handles = self.get_windows(controls_handles=controls_handles)  # Apps
        active_window = self.get_active_window(windows=windows)  # Active Window
        active_window_handle = active_window.handle if active_window else None

        try:
            active_desktop = get_current_desktop()
            all_desktops = get_all_desktops()
        except RuntimeError:
            active_desktop = {
                "id": "00000000-0000-0000-0000-000000000000",
                "name": "Default Desktop",
            }
            all_desktops = [active_desktop]

        if active_window is not None and active_window in windows:
            windows.remove(active_window)

        logger.debug(f"Active window: {active_window or 'No Active Window Found'}")
        logger.debug(f"Windows: {windows}")

        # Preparing handles for Tree
        other_windows_handles = list(controls_handles - windows_handles)

        tree_state = self.tree.get_state(
            active_window_handle, other_windows_handles, use_dom=use_dom
        )

        if use_vision:
            if use_annotation:
                nodes = tree_state.interactive_nodes
                screenshot = self.get_annotated_screenshot(nodes=nodes)
            else:
                screenshot = self.get_screenshot()

            if scale != 1.0:
                screenshot = screenshot.resize(
                    (int(screenshot.width * scale), int(screenshot.height * scale)),
                    Image.LANCZOS,
                )

            if as_bytes:
                buffered = io.BytesIO()
                screenshot.save(buffered, format="PNG")
                screenshot = buffered.getvalue()
                buffered.close()
        else:
            screenshot = None

        self.desktop_state = DesktopState(
            active_window=active_window,
            windows=windows,
            active_desktop=active_desktop,
            all_desktops=all_desktops,
            screenshot=screenshot,
            tree_state=tree_state,
        )
        # Log the time taken to capture the state
        end_time = time()
        logger.info(f"Desktop State capture took {end_time - start_time:.2f} seconds")
        return self.desktop_state

    def get_window_status(self, control: uia.Control) -> Status:
        if uia.IsIconic(control.NativeWindowHandle):
            return Status.MINIMIZED
        elif uia.IsZoomed(control.NativeWindowHandle):
            return Status.MAXIMIZED
        elif uia.IsWindowVisible(control.NativeWindowHandle):
            return Status.NORMAL
        else:
            return Status.HIDDEN

    def get_cursor_location(self) -> tuple[int, int]:
        return uia.GetCursorPos()

    def get_element_under_cursor(self) -> uia.Control:
        return uia.ControlFromCursor()

    def get_apps_from_start_menu(self) -> dict[str, str]:
        """Get installed apps. Tries Get-StartApps first, falls back to shortcut scanning."""
        command = "Get-StartApps | ConvertTo-Csv -NoTypeInformation"
        apps_info, status = self.execute_command(command)

        if status == 0 and apps_info and apps_info.strip():
            try:
                reader = csv.DictReader(io.StringIO(apps_info.strip()))
                apps = {
                    row.get("Name", "").lower(): row.get("AppID", "")
                    for row in reader
                    if row.get("Name") and row.get("AppID")
                }
                if apps:
                    return apps
            except Exception as e:
                logger.warning(f"Error parsing Get-StartApps output: {e}")

        # Fallback: scan Start Menu shortcut folders (works on all Windows versions)
        logger.info("Get-StartApps unavailable, falling back to Start Menu folder scan")
        return self._get_apps_from_shortcuts()

    def _get_apps_from_shortcuts(self) -> dict[str, str]:
        """Scan Start Menu folders for .lnk shortcuts as a fallback for Get-StartApps."""
        import glob

        apps = {}
        start_menu_paths = [
            os.path.join(
                os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                r"Microsoft\Windows\Start Menu\Programs",
            ),
            os.path.join(
                os.environ.get("APPDATA", ""),
                r"Microsoft\Windows\Start Menu\Programs",
            ),
        ]
        for base_path in start_menu_paths:
            if not os.path.isdir(base_path):
                continue
            for lnk_path in glob.glob(os.path.join(base_path, "**", "*.lnk"), recursive=True):
                name = os.path.splitext(os.path.basename(lnk_path))[0].lower()
                if name and name not in apps:
                    apps[name] = lnk_path
        return apps

    def execute_command(self, command: str, timeout: int = 10) -> tuple[str, int]:
        try:
            # Set console encoding to UTF-8 for native executable outputs
            utf8_command = f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; {command}"
            encoded = base64.b64encode(utf8_command.encode("utf-16le")).decode("ascii")
            env = os.environ.copy()
            # Fix PATHEXT if clobbered by venv activation (uv strips it to .CPL)
            if ".EXE" not in env.get("PATHEXT", ""):
                try:
                    import winreg

                    with winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                    ) as key:
                        env["PATHEXT"] = winreg.QueryValueEx(key, "PATHEXT")[0]
                except Exception:
                    env["PATHEXT"] = (
                        ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL;.PY;.PYW"
                    )

            shell = "pwsh" if shutil.which("pwsh") else "powershell"

            args = [shell, "-NoProfile"]
            # Only older Windows PowerShell (5.1) uses -OutputFormat Text successfully here
            shell_name = os.path.basename(shell).lower().replace(".exe", "")
            if shell_name == "powershell":
                args.extend(["-OutputFormat", "Text"])
            args.extend(["-EncodedCommand", encoded])

            result = subprocess.run(
                args,
                capture_output=True,  # No errors='ignore' - let subprocess return bytes
                timeout=timeout,
                cwd=os.path.expanduser(path="~"),
                env=env,
            )
            # Handle both bytes and str output (subprocess behavior varies by environment)
            stdout = result.stdout
            stderr = result.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return (stdout or stderr, result.returncode)
        except subprocess.TimeoutExpired:
            return ("Command execution timed out", 1)
        except Exception as e:
            return (f"Command execution failed: {type(e).__name__}: {e}", 1)

    def is_window_browser(self, node: uia.Control):
        """Give any node of the app and it will return True if the app is a browser, False otherwise."""
        try:
            process = Process(node.ProcessId)
            return Browser.has_process(process.name())
        except Exception:
            return False

    def get_default_language(self) -> str:
        command = "Get-Culture | Select-Object Name,DisplayName | ConvertTo-Csv -NoTypeInformation"
        response, _ = self.execute_command(command)
        reader = csv.DictReader(io.StringIO(response))
        return "".join([row.get("DisplayName") for row in reader])

    def resize_app(
        self, size: tuple[int, int] = None, loc: tuple[int, int] = None
    ) -> tuple[str, int]:
        active_window = self.desktop_state.active_window
        if active_window is None:
            return "No active window found", 1
        if active_window.status == Status.MINIMIZED:
            return f"{active_window.name} is minimized", 1
        elif active_window.status == Status.MAXIMIZED:
            return f"{active_window.name} is maximized", 1
        else:
            window_control = uia.ControlFromHandle(active_window.handle)
            if loc is None:
                x = window_control.BoundingRectangle.left
                y = window_control.BoundingRectangle.top
                loc = (x, y)
            if size is None:
                width = window_control.BoundingRectangle.width()
                height = window_control.BoundingRectangle.height()
                size = (width, height)
            x, y = loc
            width, height = size
            window_control.MoveWindow(x, y, width, height)
            return (f"{active_window.name} resized to {width}x{height} at {x},{y}.", 0)

    def is_app_running(self, name: str) -> bool:
        windows, _ = self.get_windows()
        windows_dict = {window.name: window for window in windows}
        return process.extractOne(name, list(windows_dict.keys()), score_cutoff=60) is not None

    def app(
        self,
        mode: Literal["launch", "switch", "resize"],
        name: str | None = None,
        loc: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
    ):
        match mode:
            case "launch":
                response, status, pid = self.launch_app(name)
                if status != 0:
                    return response

                # Smart wait using UIA Exists (avoids manual Python loops)
                launched = False
                if pid > 0:
                    if uia.WindowControl(ProcessId=pid).Exists(maxSearchSeconds=10):
                        launched = True

                if not launched:
                    # Fallback: Regex search for the window title
                    safe_name = re.escape(name)
                    if uia.WindowControl(RegexName=f"(?i).*{safe_name}.*").Exists(
                        maxSearchSeconds=10
                    ):
                        launched = True

                if launched:
                    return f"{name.title()} launched."
                return f"Launching {name.title()} sent, but window not detected yet."
            case "resize":
                response, status = self.resize_app(size=size, loc=loc)
                if status != 0:
                    return response
                else:
                    return response
            case "switch":
                response, status = self.switch_app(name)
                if status != 0:
                    return response
                else:
                    return response

    def launch_app(self, name: str) -> tuple[str, int, int]:
        apps_map = self.get_apps_from_start_menu()
        matched_app = process.extractOne(name, apps_map.keys(), score_cutoff=70)
        if matched_app is None:
            return (f"{name.title()} not found in start menu.", 1, 0)
        app_name, _ = matched_app
        appid = apps_map.get(app_name)
        if appid is None:
            return (f"{name.title()} not found in start menu.", 1, 0)

        pid = 0
        if os.path.exists(appid) or "\\" in appid:
            safe = ps_quote(appid)
            command = f"Start-Process {safe} -PassThru | Select-Object -ExpandProperty Id"
            response, status = self.execute_command(command)
            if status == 0 and response.strip().isdigit():
                pid = int(response.strip())
        else:
            # Validate appid format (allow UWP IDs like Microsoft.WindowsNotepad_...!App)
            # Chars to ignore for validation: \ , _ , . , - , !
            validation_id = (
                appid.replace("\\", "")
                .replace("_", "")
                .replace(".", "")
                .replace("-", "")
                .replace("!", "")
            )
            if not validation_id.isalnum():
                return (f"Invalid app identifier: {appid}", 1, 0)

            safe = ps_quote(f"shell:AppsFolder\\{appid}")
            command = f"Start-Process {safe}"
            response, status = self.execute_command(command)

        return response, status, pid

    def switch_app(self, name: str):
        try:
            # Refresh state if desktop_state is None or has no windows
            if self.desktop_state is None or not self.desktop_state.windows:
                self.get_state()
            if self.desktop_state is None:
                return ("Failed to get desktop state. Please try again.", 1)

            window_list = [
                w
                for w in [self.desktop_state.active_window] + self.desktop_state.windows
                if w is not None
            ]
            if not window_list:
                return ("No windows found on the desktop.", 1)

            windows = {window.name: window for window in window_list}
            matched_window: tuple[str, float] | None = process.extractOne(
                name, list(windows.keys()), score_cutoff=70
            )
            if matched_window is None:
                return (f"Application {name.title()} not found.", 1)
            window_name, _ = matched_window
            window = windows.get(window_name)
            target_handle = window.handle

            if uia.IsIconic(target_handle):
                uia.ShowWindow(target_handle, win32con.SW_RESTORE)
                content = f"{window_name.title()} restored from Minimized state."
            else:
                self.bring_window_to_top(target_handle)
                content = f"Switched to {window_name.title()} window."
            return content, 0
        except Exception as e:
            return (f"Error switching app: {str(e)}", 1)

    def bring_window_to_top(self, target_handle: int):
        if not win32gui.IsWindow(target_handle):
            raise ValueError("Invalid window handle")

        try:
            if win32gui.IsIconic(target_handle):
                win32gui.ShowWindow(target_handle, win32con.SW_RESTORE)

            foreground_handle = win32gui.GetForegroundWindow()

            # Validate both handles before proceeding
            if not win32gui.IsWindow(foreground_handle):
                # No valid foreground window, just try to set target as foreground
                win32gui.SetForegroundWindow(target_handle)
                win32gui.BringWindowToTop(target_handle)
                return

            foreground_thread, _ = win32process.GetWindowThreadProcessId(foreground_handle)
            target_thread, _ = win32process.GetWindowThreadProcessId(target_handle)

            if not foreground_thread or not target_thread or foreground_thread == target_thread:
                win32gui.SetForegroundWindow(target_handle)
                win32gui.BringWindowToTop(target_handle)
                return

            ctypes.windll.user32.AllowSetForegroundWindow(-1)

            attached = False
            try:
                win32process.AttachThreadInput(foreground_thread, target_thread, True)
                attached = True

                win32gui.SetForegroundWindow(target_handle)
                win32gui.BringWindowToTop(target_handle)

                win32gui.SetWindowPos(
                    target_handle,
                    win32con.HWND_TOP,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                )

            finally:
                if attached:
                    win32process.AttachThreadInput(foreground_thread, target_thread, False)

        except Exception as e:
            logger.exception(f"Failed to bring window to top: {e}")

    def get_coordinates_from_label(self, label: int) -> tuple[int, int]:
        tree_state = self.desktop_state.tree_state
        if label < len(tree_state.interactive_nodes):
            element_node = tree_state.interactive_nodes[label]
        else:
            scroll_idx = label - len(tree_state.interactive_nodes)
            if scroll_idx < len(tree_state.scrollable_nodes):
                element_node = tree_state.scrollable_nodes[scroll_idx]
            else:
                raise IndexError(f"Label {label} out of range")
        return element_node.center.x, element_node.center.y

    def click(self, loc: tuple[int, int] | list[int], button: str = "left", clicks: int = 2):
        if isinstance(loc, list):
            x, y = loc[0], loc[1]
        else:
            x, y = loc
        if clicks == 0:
            uia.SetCursorPos(x, y)
            return
        match button:
            case "left":
                if clicks >= 2:
                    dbl_wait = uia.GetDoubleClickTime() / 2000.0
                    for i in range(clicks):
                        uia.Click(x, y, waitTime=dbl_wait if i < clicks - 1 else 0.5)
                else:
                    uia.Click(x, y)
            case "right":
                for _ in range(clicks):
                    uia.RightClick(x, y)
            case "middle":
                for _ in range(clicks):
                    uia.MiddleClick(x, y)

    def type(
        self,
        loc: tuple[int, int],
        text: str,
        caret_position: Literal["start", "idle", "end"] = "idle",
        clear: bool | str = False,
        press_enter: bool | str = False,
    ):
        x, y = loc
        uia.Click(x, y)
        if caret_position == "start":
            uia.SendKeys("{Home}", waitTime=0.05)
        elif caret_position == "end":
            uia.SendKeys("{End}", waitTime=0.05)
        if clear is True or (isinstance(clear, str) and clear.lower() == "true"):
            sleep(0.5)
            uia.SendKeys("{Ctrl}a", waitTime=0.05)
            uia.SendKeys("{Back}", waitTime=0.05)
        escaped_text = _escape_text_for_sendkeys(text)
        uia.SendKeys(escaped_text, interval=0.02, waitTime=0.05)
        if press_enter is True or (isinstance(press_enter, str) and press_enter.lower() == "true"):
            uia.SendKeys("{Enter}", waitTime=0.05)

    def scroll(
        self,
        loc: tuple[int, int] = None,
        type: Literal["horizontal", "vertical"] = "vertical",
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
    ) -> str | None:
        if loc:
            self.move(loc)
        match type:
            case "vertical":
                match direction:
                    case "up":
                        uia.WheelUp(wheel_times)
                    case "down":
                        uia.WheelDown(wheel_times)
                    case _:
                        return 'Invalid direction. Use "up" or "down".'
            case "horizontal":
                match direction:
                    case "left":
                        uia.PressKey(uia.Keys.VK_SHIFT, waitTime=0.05)
                        uia.WheelUp(wheel_times)
                        sleep(0.05)
                        uia.ReleaseKey(uia.Keys.VK_SHIFT, waitTime=0.05)
                    case "right":
                        uia.PressKey(uia.Keys.VK_SHIFT, waitTime=0.05)
                        uia.WheelDown(wheel_times)
                        sleep(0.05)
                        uia.ReleaseKey(uia.Keys.VK_SHIFT, waitTime=0.05)
                    case _:
                        return 'Invalid direction. Use "left" or "right".'
            case _:
                return 'Invalid type. Use "horizontal" or "vertical".'
        return None

    def drag(self, loc: tuple[int, int] | list[int]):
        if isinstance(loc, list):
            x, y = loc[0], loc[1]
        else:
            x, y = loc
        sleep(0.5)
        cx, cy = uia.GetCursorPos()
        uia.DragDrop(cx, cy, x, y, moveSpeed=1)

    def move(self, loc: tuple[int, int]):
        x, y = loc
        uia.MoveTo(x, y, moveSpeed=10)

    def shortcut(self, shortcut: str):
        keys = shortcut.split("+")
        sendkeys_str = ""
        for key in keys:
            key = key.strip()
            if len(key) == 1:
                sendkeys_str += key
            else:
                name = _KEY_ALIASES.get(key.lower(), key)
                sendkeys_str += "{" + name + "}"
        uia.SendKeys(sendkeys_str, interval=0.01)

    def multi_select(
        self, press_ctrl: bool | str = False, locs: list[tuple[int, int]] | None = None
    ):
        if locs is None:
            locs = []
        press_ctrl = press_ctrl is True or (
            isinstance(press_ctrl, str) and press_ctrl.lower() == "true"
        )
        if press_ctrl:
            uia.PressKey(uia.Keys.VK_CONTROL, waitTime=0.05)
        try:
            for loc in locs:
                x, y = loc
                uia.Click(x, y, waitTime=0.2)
                sleep(0.5)
        finally:
            if press_ctrl:
                uia.ReleaseKey(uia.Keys.VK_CONTROL, waitTime=0.05)

    def multi_edit(self, locs: list[tuple[int, int, str]]):
        for loc in locs:
            x, y, text = loc
            self.type((x, y), text=text, clear=True)

    def scrape(self, url: str) -> str:
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise ValueError(f"HTTP error for {url}: {e}") from e
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(f"Failed to connect to {url}: {e}") from e
        except requests.exceptions.Timeout as e:
            raise TimeoutError(f"Request timed out for {url}: {e}") from e
        html = response.text
        content = markdownify(html=html)
        return content

    def get_window_from_element(self, element: uia.Control) -> Window | None:
        if element is None:
            return None
        top_window = element.GetTopLevelControl()
        if top_window is None:
            return None
        handle = top_window.NativeWindowHandle
        windows, _ = self.get_windows()
        for window in windows:
            if window.handle == handle:
                return window
        return None

    def is_window_visible(self, window: uia.Control) -> bool:
        is_minimized = self.get_window_status(window) != Status.MINIMIZED
        size = window.BoundingRectangle
        area = size.width() * size.height()
        is_overlay = self.is_overlay_window(window)
        return not is_overlay and is_minimized and area > 10

    def is_overlay_window(self, element: uia.Control) -> bool:
        no_children = len(element.GetChildren()) == 0
        is_name = "Overlay" in element.Name.strip()
        return no_children and is_name

    def get_controls_handles(self, optimized: bool = False):
        handles = set()

        # For even more faster results (still under development)
        def callback(hwnd, _):
            try:
                # Validate handle before checking properties
                if (
                    win32gui.IsWindow(hwnd)
                    and win32gui.IsWindowVisible(hwnd)
                    and is_window_on_current_desktop(hwnd)
                ):
                    handles.add(hwnd)
            except Exception:
                # Skip invalid handles without logging (common during window enumeration)
                pass

        win32gui.EnumWindows(callback, None)

        if desktop_hwnd := win32gui.FindWindow("Progman", None):
            handles.add(desktop_hwnd)
        if taskbar_hwnd := win32gui.FindWindow("Shell_TrayWnd", None):
            handles.add(taskbar_hwnd)
        if secondary_taskbar_hwnd := win32gui.FindWindow("Shell_SecondaryTrayWnd", None):
            handles.add(secondary_taskbar_hwnd)
        return handles

    def get_active_window(self, windows: list[Window] | None = None) -> Window | None:
        try:
            if windows is None:
                windows, _ = self.get_windows()
            active_window = self.get_foreground_window()
            if active_window.ClassName == "Progman":
                return None
            active_window_handle = active_window.NativeWindowHandle
            for window in windows:
                if window.handle != active_window_handle:
                    continue
                return window
            # In case active window is not present in the windows list
            return Window(
                **{
                    "name": active_window.Name,
                    "is_browser": self.is_window_browser(active_window),
                    "depth": 0,
                    "bounding_box": BoundingBox(
                        left=active_window.BoundingRectangle.left,
                        top=active_window.BoundingRectangle.top,
                        right=active_window.BoundingRectangle.right,
                        bottom=active_window.BoundingRectangle.bottom,
                        width=active_window.BoundingRectangle.width(),
                        height=active_window.BoundingRectangle.height(),
                    ),
                    "status": self.get_window_status(active_window),
                    "handle": active_window_handle,
                    "process_id": active_window.ProcessId,
                }
            )
        except Exception as ex:
            logger.error(f"Error in get_active_window: {ex}")
        return None

    def get_foreground_window(self) -> uia.Control:
        handle = uia.GetForegroundWindow()
        active_window = self.get_window_from_element_handle(handle)
        return active_window

    def get_window_from_element_handle(self, element_handle: int) -> uia.Control:
        current = uia.ControlFromHandle(element_handle)
        root_handle = uia.GetRootControl().NativeWindowHandle

        while True:
            parent = current.GetParentControl()
            if parent is None or parent.NativeWindowHandle == root_handle:
                return current
            current = parent

    def get_windows(
        self, controls_handles: set[int] | None = None
    ) -> tuple[list[Window], set[int]]:
        try:
            windows = []
            window_handles = set()
            controls_handles = controls_handles or self.get_controls_handles()
            for depth, hwnd in enumerate(controls_handles):
                try:
                    child = uia.ControlFromHandle(hwnd)
                except Exception:
                    continue

                # Filter out Overlays (e.g. NVIDIA, Steam)
                if self.is_overlay_window(child):
                    continue

                if isinstance(child, (uia.WindowControl, uia.PaneControl)):
                    window_pattern = child.GetPattern(uia.PatternId.WindowPattern)
                    if window_pattern is None:
                        continue

                    if window_pattern.CanMinimize and window_pattern.CanMaximize:
                        status = self.get_window_status(child)

                        bounding_rect = child.BoundingRectangle
                        if bounding_rect.isempty() and status != Status.MINIMIZED:
                            continue

                        windows.append(
                            Window(
                                **{
                                    "name": child.Name,
                                    "depth": depth,
                                    "status": status,
                                    "bounding_box": BoundingBox(
                                        left=bounding_rect.left,
                                        top=bounding_rect.top,
                                        right=bounding_rect.right,
                                        bottom=bounding_rect.bottom,
                                        width=bounding_rect.width(),
                                        height=bounding_rect.height(),
                                    ),
                                    "handle": child.NativeWindowHandle,
                                    "process_id": child.ProcessId,
                                    "is_browser": self.is_window_browser(child),
                                }
                            )
                        )
                        window_handles.add(child.NativeWindowHandle)
        except Exception as ex:
            logger.error(f"Error in get_windows: {ex}")
            windows = []
        return windows, window_handles

    def get_xpath_from_element(self, element: uia.Control):
        current = element
        if current is None:
            return ""
        path_parts = []
        while current is not None:
            parent = current.GetParentControl()
            if parent is None:
                # we are at the root node
                path_parts.append(f"{current.ControlTypeName}")
                break
            children = parent.GetChildren()
            same_type_children = [
                "-".join(map(lambda x: str(x), child.GetRuntimeId()))
                for child in children
                if child.ControlType == current.ControlType
            ]
            index = same_type_children.index(
                "-".join(map(lambda x: str(x), current.GetRuntimeId()))
            )
            if same_type_children:
                path_parts.append(f"{current.ControlTypeName}[{index + 1}]")
            else:
                path_parts.append(f"{current.ControlTypeName}")
            current = parent
        path_parts.reverse()
        xpath = "/".join(path_parts)
        return xpath

    def get_windows_version(self) -> str:
        response, status = self.execute_command("(Get-CimInstance Win32_OperatingSystem).Caption")
        if status == 0:
            return response.strip()
        return "Windows"

    def get_user_account_type(self) -> str:
        response, status = self.execute_command(
            "(Get-LocalUser -Name $env:USERNAME).PrincipalSource"
        )
        return (
            "Local Account"
            if response.strip() == "Local"
            else "Microsoft Account"
            if status == 0
            else "Local Account"
        )

    def get_dpi_scaling(self):
        try:
            user32 = ctypes.windll.user32
            dpi = user32.GetDpiForSystem()
            return dpi / 96.0 if dpi > 0 else 1.0
        except Exception:
            # Fallback to standard DPI if system call fails
            return 1.0

    def get_screen_size(self) -> Size:
        width, height = uia.GetVirtualScreenSize()
        return Size(width=width, height=height)

    def get_screenshot(self) -> Image.Image:
        try:
            return ImageGrab.grab(all_screens=True)
        except Exception:
            logger.warning("Failed to capture virtual screen, using primary screen")
            return ImageGrab.grab()

    def get_annotated_screenshot(self, nodes: list[TreeElementNode]) -> Image.Image:
        screenshot = self.get_screenshot()
        # Add padding
        padding = 5
        width = int(screenshot.width + (1.5 * padding))
        height = int(screenshot.height + (1.5 * padding))
        padded_screenshot = Image.new("RGB", (width, height), color=(255, 255, 255))
        padded_screenshot.paste(screenshot, (padding, padding))

        draw = ImageDraw.Draw(padded_screenshot)
        font_size = 12
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except IOError:
            font = ImageFont.load_default()

        def get_random_color():
            return "#{:06x}".format(random.randint(0, 0xFFFFFF))

        left_offset, top_offset, _, _ = uia.GetVirtualScreenRect()

        def draw_annotation(label, node: TreeElementNode):
            box = node.bounding_box
            color = get_random_color()

            # Scale and pad the bounding box also clip the bounding box
            # Adjust for virtual screen offset so coordinates map to the screenshot image
            adjusted_box = (
                int(box.left - left_offset) + padding,
                int(box.top - top_offset) + padding,
                int(box.right - left_offset) + padding,
                int(box.bottom - top_offset) + padding,
            )
            # Draw bounding box
            draw.rectangle(adjusted_box, outline=color, width=2)

            # Label dimensions
            label_width = draw.textlength(str(label), font=font)
            label_height = font_size
            left, top, right, bottom = adjusted_box

            # Label position above bounding box
            label_x1 = right - label_width
            label_y1 = top - label_height - 4
            label_x2 = label_x1 + label_width
            label_y2 = label_y1 + label_height + 4

            # Draw label background and text
            draw.rectangle([(label_x1, label_y1), (label_x2, label_y2)], fill=color)
            draw.text(
                (label_x1 + 2, label_y1 + 2),
                str(label),
                fill=(255, 255, 255),
                font=font,
            )

        # Draw annotations sequentially (ImageDraw is not thread-safe)
        for i, node in enumerate(nodes):
            draw_annotation(i, node)
        return padded_screenshot

    def send_notification(self, title: str, message: str) -> str:
        safe_title = ps_quote_for_xml(title)
        safe_message = ps_quote_for_xml(message)

        ps_script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
            f"$notifTitle = {safe_title}\n"
            f"$notifMessage = {safe_message}\n"
            '$template = @"\n'
            "<toast>\n"
            "    <visual>\n"
            '        <binding template="ToastGeneric">\n'
            "            <text>$notifTitle</text>\n"
            "            <text>$notifMessage</text>\n"
            "        </binding>\n"
            "    </visual>\n"
            "</toast>\n"
            '"@\n'
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
            "$xml.LoadXml($template)\n"
            '$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Windows MCP")\n'
            "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml\n"
            "$notifier.Show($toast)"
        )
        response, status = self.execute_command(ps_script)
        if status == 0:
            return f'Notification sent: "{title}" - {message}'
        else:
            return f"Notification may have been sent. PowerShell output: {response[:200]}"

    def list_processes(
        self,
        name: str | None = None,
        sort_by: Literal["memory", "cpu", "name"] = "memory",
        limit: int = 20,
    ) -> str:
        import psutil
        from tabulate import tabulate

        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                info = p.info
                mem_mb = info["memory_info"].rss / (1024 * 1024) if info["memory_info"] else 0
                procs.append(
                    {
                        "pid": info["pid"],
                        "name": info["name"] or "Unknown",
                        "cpu": info["cpu_percent"] or 0,
                        "mem_mb": round(mem_mb, 1),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if name:
            from thefuzz import fuzz

            procs = [p for p in procs if fuzz.partial_ratio(name.lower(), p["name"].lower()) > 60]
        sort_key = {
            "memory": lambda x: x["mem_mb"],
            "cpu": lambda x: x["cpu"],
            "name": lambda x: x["name"].lower(),
        }
        procs.sort(key=sort_key.get(sort_by, sort_key["memory"]), reverse=(sort_by != "name"))
        procs = procs[:limit]
        if not procs:
            return f"No processes found{f' matching {name}' if name else ''}."
        table = tabulate(
            [[p["pid"], p["name"], f"{p['cpu']:.1f}%", f"{p['mem_mb']:.1f} MB"] for p in procs],
            headers=["PID", "Name", "CPU%", "Memory"],
            tablefmt="simple",
        )
        return f"Processes ({len(procs)} shown):\n{table}"

    def kill_process(
        self, name: str | None = None, pid: int | None = None, force: bool = False
    ) -> str:
        import psutil

        if pid is None and name is None:
            return "Error: Provide either pid or name parameter for kill mode."
        killed = []
        if pid is not None:
            try:
                p = psutil.Process(pid)
                pname = p.name()
                if force:
                    p.kill()
                else:
                    p.terminate()
                killed.append(f"{pname} (PID {pid})")
            except psutil.NoSuchProcess:
                return f"No process with PID {pid} found."
            except psutil.AccessDenied:
                return f"Access denied to kill PID {pid}. Try running as administrator."
        else:
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    if p.info["name"] and p.info["name"].lower() == name.lower():
                        if force:
                            p.kill()
                        else:
                            p.terminate()
                        killed.append(f"{p.info['name']} (PID {p.info['pid']})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        if not killed:
            return f'No process matching "{name}" found or access denied.'
        return f"{'Force killed' if force else 'Terminated'}: {', '.join(killed)}"

    def registry_get(self, path: str, name: str) -> str:
        q_path = ps_quote(path)
        q_name = ps_quote(name)
        command = f"Get-ItemProperty -Path {q_path} -Name {q_name} | Select-Object -ExpandProperty {q_name}"
        response, status = self.execute_command(command)
        if status != 0:
            return f"Error reading registry: {response.strip()}"
        return f'Registry value [{path}] "{name}" = {response.strip()}'

    def registry_set(self, path: str, name: str, value: str, reg_type: str = "String") -> str:
        q_path = ps_quote(path)
        q_name = ps_quote(name)
        q_value = ps_quote(value)
        allowed_types = {"String", "ExpandString", "Binary", "DWord", "MultiString", "QWord"}
        if reg_type not in allowed_types:
            return f"Error: invalid registry type '{reg_type}'. Allowed: {', '.join(sorted(allowed_types))}"
        command = (
            f"if (-not (Test-Path {q_path})) {{ New-Item -Path {q_path} -Force | Out-Null }}; "
            f"Set-ItemProperty -Path {q_path} -Name {q_name} -Value {q_value} -Type {reg_type} -Force"
        )
        response, status = self.execute_command(command)
        if status != 0:
            return f"Error writing registry: {response.strip()}"
        return f'Registry value [{path}] "{name}" set to "{value}" (type: {reg_type}).'

    def registry_delete(self, path: str, name: str | None = None) -> str:
        q_path = ps_quote(path)
        if name:
            q_name = ps_quote(name)
            command = f"Remove-ItemProperty -Path {q_path} -Name {q_name} -Force"
            response, status = self.execute_command(command)
            if status != 0:
                return f"Error deleting registry value: {response.strip()}"
            return f'Registry value [{path}] "{name}" deleted.'
        else:
            command = f"Remove-Item -Path {q_path} -Recurse -Force"
            response, status = self.execute_command(command)
            if status != 0:
                return f"Error deleting registry key: {response.strip()}"
            return f"Registry key [{path}] deleted."

    def registry_list(self, path: str) -> str:
        q_path = ps_quote(path)
        command = (
            f"$values = (Get-ItemProperty -Path {q_path} -ErrorAction Stop | "
            f"Select-Object * -ExcludeProperty PS* | Format-List | Out-String).Trim(); "
            f"$subkeys = (Get-ChildItem -Path {q_path} -ErrorAction SilentlyContinue | "
            f'Select-Object -ExpandProperty PSChildName) -join "`n"; '
            f'if ($values) {{ Write-Output "Values:`n$values" }}; '
            f'if ($subkeys) {{ Write-Output "`nSub-Keys:`n$subkeys" }}; '
            f"if (-not $values -and -not $subkeys) {{ Write-Output 'No values or sub-keys found.' }}"
        )
        response, status = self.execute_command(command)
        if status != 0:
            return f"Error listing registry: {response.strip()}"
        return f"Registry key [{path}]:\n{response.strip()}"

    @contextmanager
    def auto_minimize(self):
        try:
            handle = uia.GetForegroundWindow()
            uia.ShowWindow(handle, win32con.SW_MINIMIZE)
            yield
        finally:
            uia.ShowWindow(handle, win32con.SW_RESTORE)

    def get_cursor_position(self) -> str:
        x, y = uia.GetCursorPos()
        return f"Cursor position: ({x}, {y})"

    def get_pixel_color(self, loc: list[int]) -> str:
        if len(loc) != 2:
            return "Error: loc must be [x, y]"
        x, y = loc[0], loc[1]
        try:
            img = ImageGrab.grab(bbox=(x, y, x + 1, y + 1), all_screens=True)
            pixel = img.getpixel((0, 0))
            r, g, b = pixel[0], pixel[1], pixel[2]
            hex_color = f"#{r:02X}{g:02X}{b:02X}"
            name = approximate_color_name(r, g, b)
            return f"Color at ({x}, {y}): R={r}, G={g}, B={b} ({hex_color}) - {name}"
        except Exception as e:
            return f"Error reading pixel at ({x}, {y}): {str(e)}"

    def key_hold(self, action: str, keys: list[str]) -> str:
        if action not in ("down", "up"):
            return f"Error: action must be 'down' or 'up', got '{action}'"
        results = []
        for key_name in keys:
            k = key_name.strip().lower()
            vk = _VK_MAP.get(k)
            if vk is None and len(k) == 1:
                vk = ord(k.upper())
            if vk is None:
                available = ", ".join(sorted(_VK_MAP.keys()))
                return f"Error: Unknown key '{key_name}'. Available keys: {available}"
            if action == "down":
                uia.PressKey(vk, waitTime=0.05)
                results.append(key_name)
            elif action == "up":
                uia.ReleaseKey(vk, waitTime=0.05)
                results.append(key_name)
        verb = "Pressed" if action == "down" else "Released"
        return f"{verb} keys: {', '.join(results)}"

    def get_screen_info(self) -> str:
        try:
            ps_cmd = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.Screen]::AllScreens | ForEach-Object { "
                "$_.DeviceName + '|' + $_.Bounds.Width + '|' + $_.Bounds.Height + '|' "
                "+ $_.Bounds.X + '|' + $_.Bounds.Y + '|' + $_.Primary }"
            )
            result, status = self.execute_command(ps_cmd, timeout=10)
        except Exception:
            size = self.get_screen_size()
            return f"Monitors (1):\n[1] {size.width}x{size.height} (primary) at (0, 0)"

        if status != 0 or not result.strip():
            size = self.get_screen_size()
            return f"Monitors (1):\n[1] {size.width}x{size.height} (primary) at (0, 0)"

        lines = []
        for i, line in enumerate(result.strip().split("\n"), 1):
            parts = line.strip().split("|")
            if len(parts) >= 6:
                w, h, x, y = parts[1], parts[2], parts[3], parts[4]
                primary_str = " (primary)" if parts[5].strip().lower() == "true" else ""
                lines.append(f"[{i}] {w}x{h}{primary_str} at ({x}, {y})")

        if not lines:
            size = self.get_screen_size()
            return f"Monitors (1):\n[1] {size.width}x{size.height} (primary) at (0, 0)"

        try:
            dpi_scale = self.get_dpi_scaling()
            dpi_info = f"\nDPI scaling: {dpi_scale}x"
        except Exception:
            dpi_info = ""

        return f"Monitors ({len(lines)}):\n" + "\n".join(lines) + dpi_info

    def highlight_region(
        self, loc: list[int], size: list[int], duration: float = 2.0, color: str = "red"
    ) -> str:
        if len(loc) != 2:
            return "Error: loc must be [x, y]"
        if len(size) != 2:
            return "Error: size must be [width, height]"
        x, y = loc[0], loc[1]
        w, h = size[0], size[1]
        if w <= 0 or h <= 0:
            return "Error: width and height must be positive"
        duration = min(max(duration, 0.1), 30.0)  # Clamp between 100ms and 30s
        color_val = _HIGHLIGHT_COLORS.get(color.lower(), 0x0000FF)
        hdc = None
        pen = None
        try:
            hdc = ctypes.windll.user32.GetDC(0)
            if not hdc:
                return "Error: Could not acquire screen device context"
            pen = ctypes.windll.gdi32.CreatePen(0, 3, color_val)  # PS_SOLID, 3px
            if not pen:
                return "Error: Could not create GDI pen"
            old_pen = ctypes.windll.gdi32.SelectObject(hdc, pen)
            brush = ctypes.windll.gdi32.GetStockObject(5)  # NULL_BRUSH
            old_brush = ctypes.windll.gdi32.SelectObject(hdc, brush)
            ctypes.windll.gdi32.Rectangle(hdc, x, y, x + w, y + h)
            ctypes.windll.gdi32.SelectObject(hdc, old_pen)
            ctypes.windll.gdi32.SelectObject(hdc, old_brush)
            sleep(duration)
            # Invalidate the region to clear the highlight
            ctypes.windll.user32.InvalidateRect(0, None, True)
            return f"Highlighted region ({x}, {y}, {w}x{h}) in {color} for {duration}s."
        except Exception as e:
            return f"Error highlighting region: {str(e)}"
        finally:
            if pen:
                ctypes.windll.gdi32.DeleteObject(pen)
            if hdc:
                ctypes.windll.user32.ReleaseDC(0, hdc)

    def mouse_path(self, path: list[list[int]], duration: float = 0.5) -> str:
        if not path or len(path) < 2:
            return "Error: path must contain at least 2 waypoints [[x1,y1], [x2,y2], ...]"
        if duration < 0:
            return "Error: duration must be non-negative"
        for i, point in enumerate(path):
            if len(point) != 2:
                return f"Error: waypoint {i} must be [x, y], got {point}"

        if duration == 0:
            x, y = path[-1]
            uia.MoveTo(x, y, moveSpeed=0)
            return f"Mouse moved through {len(path)} waypoints in 0s."

        total_segments = len(path) - 1
        segment_duration = duration / total_segments if total_segments > 0 else 0
        steps_per_segment = max(1, int(segment_duration * 60))  # ~60 fps

        for seg in range(total_segments):
            x1, y1 = path[seg]
            x2, y2 = path[seg + 1]
            step_delay = segment_duration / steps_per_segment if steps_per_segment > 0 else 0
            for step in range(steps_per_segment + 1):
                t = step / steps_per_segment if steps_per_segment > 0 else 1.0
                ix = int(x1 + (x2 - x1) * t)
                iy = int(y1 + (y2 - y1) * t)
                uia.MoveTo(ix, iy, moveSpeed=0)
                if step_delay > 0:
                    sleep(step_delay)

        return f"Mouse moved through {len(path)} waypoints in {duration}s."

    def read_screen_text(self, region: list[int] | None = None, language: str = "en") -> str:
        tmp_path = None
        try:
            if region is not None:
                if len(region) != 4:
                    return "Error: region must be [x, y, width, height]"
                x, y, w, h = region
                if w <= 0 or h <= 0:
                    return "Error: width and height must be positive"
                img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
            else:
                img = ImageGrab.grab(all_screens=True)

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
                img.save(tmp_path, format="PNG")

            # Primary: Windows built-in OCR via PowerShell
            safe_path = ps_quote(tmp_path)
            ps_script = (
                "Add-Type -AssemblyName 'System.Runtime.WindowsRuntime'\n"
                "[void][Windows.Foundation.IAsyncOperation``1,Windows.Foundation,ContentType=WindowsRuntime]\n"
                "[void][Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]\n"
                "[void][Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]\n"
                "$stream = [System.IO.File]::OpenRead(" + safe_path + ")\n"
                "$raStream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($stream)\n"
                "$decoder = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($raStream).GetAwaiter().GetResult()\n"
                "$bitmap = $decoder.GetSoftwareBitmapAsync().GetAwaiter().GetResult()\n"
                "$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()\n"
                "if ($engine) {\n"
                "  $result = $engine.RecognizeAsync($bitmap).GetAwaiter().GetResult()\n"
                "  Write-Output $result.Text\n"
                "} else { Write-Output 'OCR_ENGINE_UNAVAILABLE' }\n"
                "$stream.Dispose()"
            )
            result, status = self.execute_command(ps_script, timeout=30)

            if status == 0 and "OCR_ENGINE_UNAVAILABLE" not in result:
                text = result.strip()
                if text:
                    return f"OCR text:\n{text}"
                return "No text detected in the specified region."

            # Fallback: pytesseract
            try:
                import pytesseract

                text = pytesseract.image_to_string(img, lang=language).strip()
                if text:
                    return f"OCR text (pytesseract):\n{text}"
                return "No text detected in the specified region."
            except ImportError:
                return (
                    "Error: Windows OCR unavailable and pytesseract not installed. "
                    "Install with: pip install 'windows-mcp[ocr]'"
                )
        except Exception as e:
            return f"Error reading screen text: {str(e)}"
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def wait_for_change(
        self,
        region: list[int],
        timeout: float = 30.0,
        threshold: float = 0.05,
        poll_interval: float = 0.5,
    ) -> str:
        if len(region) != 4:
            return "Error: region must be [x, y, width, height]"
        x, y, w, h = region
        if w <= 0 or h <= 0:
            return "Error: width and height must be positive"
        if not 0.0 <= threshold <= 1.0:
            return "Error: threshold must be between 0.0 and 1.0"
        timeout = min(timeout, 60.0)  # Hard cap at 60s
        poll_interval = max(poll_interval, 0.1)  # Prevent CPU spinning
        bbox = (x, y, x + w, y + h)

        try:
            baseline = list(ImageGrab.grab(bbox=bbox, all_screens=True).getdata())
        except Exception as e:
            return f"Error capturing baseline: {str(e)}"

        total_pixels = len(baseline)
        if total_pixels == 0:
            return "Error: region has zero pixels."

        start = time()
        while (time() - start) < timeout:
            sleep(poll_interval)
            try:
                current = list(ImageGrab.grab(bbox=bbox, all_screens=True).getdata())
            except Exception:
                continue

            diff_count = sum(1 for a, b in zip(baseline, current) if a != b)
            diff_ratio = diff_count / total_pixels

            if diff_ratio >= threshold:
                elapsed = round(time() - start, 1)
                pct = round(diff_ratio * 100, 1)
                return (
                    f"Change detected in region ({x}, {y}, {w}x{h}) after {elapsed}s. "
                    f"{pct}% of pixels changed."
                )

        return (
            f"Timeout: no significant change detected in region ({x}, {y}, {w}x{h}) "
            f"after {timeout}s (threshold: {threshold * 100}%)."
        )

    def find_image(
        self,
        template_path: str,
        region: list[int] | None = None,
        threshold: float = 0.8,
    ) -> str:
        if not 0.0 <= threshold <= 1.0:
            return "Error: threshold must be between 0.0 and 1.0"

        try:
            import cv2
            import numpy as np
        except ImportError:
            return (
                "Error: opencv-python-headless and numpy are required. "
                "Install with: pip install 'windows-mcp[vision]'"
            )

        # Resolve and validate path to prevent traversal attacks
        import pathlib

        try:
            resolved = pathlib.Path(template_path).resolve()
        except (ValueError, OSError):
            return f"Error: Invalid template path: {template_path}"

        if not resolved.is_file():
            return f"Error: Template file not found: {template_path}"

        # Only allow common image extensions
        allowed_ext = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
        if resolved.suffix.lower() not in allowed_ext:
            return (
                f"Error: Template must be an image file ({', '.join(sorted(allowed_ext))}), "
                f"got '{resolved.suffix}'"
            )

        try:
            template = cv2.imread(str(resolved), cv2.IMREAD_COLOR)
            if template is None:
                return f"Error: Could not read template image: {template_path}"

            if region is not None:
                if len(region) != 4:
                    return "Error: region must be [x, y, width, height]"
                x, y, w, h = region
                if w <= 0 or h <= 0:
                    return "Error: width and height must be positive"
                screen_img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
            else:
                x, y = 0, 0
                screen_img = ImageGrab.grab(all_screens=True)

            screen_rgb = np.array(screen_img)
            screen_bgr = cv2.cvtColor(screen_rgb, cv2.COLOR_RGB2BGR)

            th, tw = template.shape[:2]
            sh, sw = screen_bgr.shape[:2]
            if th > sh or tw > sw:
                return f"Error: Template ({tw}x{th}) is larger than search area ({sw}x{sh})."

            result = cv2.matchTemplate(screen_bgr, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val >= threshold:
                cx = x + max_loc[0] + tw // 2
                cy = y + max_loc[1] + th // 2
                confidence = round(max_val, 3)
                return (
                    f"Match found at ({cx}, {cy}) with confidence {confidence}. "
                    f"Template size: {tw}x{th}."
                )
            else:
                return (
                    f"No match found (best confidence: {round(max_val, 3)}, "
                    f"threshold: {threshold}). Template: {tw}x{th}."
                )
        except Exception as e:
            return f"Error during image matching: {str(e)}"

    # ============== SYSTEM CONTROL METHODS ==============

    def volume_control(self, action: str, level: int | None = None) -> str:
        """Control system volume via PowerShell COM AudioEndpointVolume."""
        if action == "get":
            ps = (
                "Add-Type -TypeDefinition @'\n"
                "using System.Runtime.InteropServices;\n"
                '[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n'
                "interface IAudioEndpointVolume {\n"
                "  int _0(); int _1(); int _2(); int _3(); int _4(); int _5(); int _6();\n"
                "  int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);\n"
                "  int GetMasterVolumeLevelScalar(out float pfLevel);\n"
                "  int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, System.Guid pguidEventContext);\n"
                "  int GetMute(out bool pbMute);\n"
                "}\n"
                '[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n'
                "interface IMMDevice { int Activate(ref System.Guid iid, int dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface); }\n"
                '[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n'
                "interface IMMDeviceEnumerator { int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppDevice); }\n"
                '[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject { }\n'
                "public class Audio {\n"
                "  static IAudioEndpointVolume GetVol() {\n"
                "    var enumerator = new MMDeviceEnumeratorComObject() as IMMDeviceEnumerator;\n"
                "    IMMDevice dev; enumerator.GetDefaultAudioEndpoint(0, 1, out dev);\n"
                "    var iid = typeof(IAudioEndpointVolume).GUID; object o;\n"
                "    dev.Activate(ref iid, 1, IntPtr.Zero, out o);\n"
                "    return (IAudioEndpointVolume)o;\n"
                "  }\n"
                "  public static float Volume { get { float v; GetVol().GetMasterVolumeLevelScalar(out v); return v; } set { GetVol().SetMasterVolumeLevelScalar(value, System.Guid.Empty); } }\n"
                "  public static bool Mute { get { bool m; GetVol().GetMute(out m); return m; } set { GetVol().SetMute(value, System.Guid.Empty); } }\n"
                "}\n"
                "'@ -ErrorAction SilentlyContinue\n"
            )
            ps += 'Write-Output "Volume:$([Math]::Round([Audio]::Volume * 100)),Mute:$([Audio]::Mute)"'
            result, status = self.execute_command(ps, timeout=10)
            if status != 0:
                return f"Error: {result}"
            return f"System volume: {result.strip()}"

        if action == "set":
            if level is None:
                return "Error: level is required for 'set' action"
            if level < 0 or level > 100:
                return "Error: level must be 0-100"
            # COM interop for volume set — intentionally omits SetMute/GetMute
            # since they are unused (vtable position of SetMasterVolumeLevelScalar is stable)
            ps = (
                "Add-Type -TypeDefinition @'\n"
                "using System.Runtime.InteropServices;\n"
                '[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n'
                "interface IAudioEndpointVolume {\n"
                "  int _0(); int _1(); int _2(); int _3(); int _4(); int _5(); int _6();\n"
                "  int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);\n"
                "  int GetMasterVolumeLevelScalar(out float pfLevel);\n"
                "}\n"
                '[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n'
                "interface IMMDevice { int Activate(ref System.Guid iid, int dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface); }\n"
                '[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n'
                "interface IMMDeviceEnumerator { int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppDevice); }\n"
                '[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject { }\n'
                "public class Audio {\n"
                "  static IAudioEndpointVolume GetVol() {\n"
                "    var enumerator = new MMDeviceEnumeratorComObject() as IMMDeviceEnumerator;\n"
                "    IMMDevice dev; enumerator.GetDefaultAudioEndpoint(0, 1, out dev);\n"
                "    var iid = typeof(IAudioEndpointVolume).GUID; object o;\n"
                "    dev.Activate(ref iid, 1, IntPtr.Zero, out o);\n"
                "    return (IAudioEndpointVolume)o;\n"
                "  }\n"
                "  public static void SetVol(float v) { GetVol().SetMasterVolumeLevelScalar(v, System.Guid.Empty); }\n"
                "}\n"
                f"'@ -ErrorAction SilentlyContinue\n[Audio]::SetVol({level / 100.0})"
            )
            result, status = self.execute_command(ps, timeout=10)
            if status != 0:
                return f"Error: {result}"
            return f"Volume set to {level}%"

        if action in ("mute", "unmute", "toggle"):
            mute_val = (
                "true"
                if action == "mute"
                else "false"
                if action == "unmute"
                else "(-not [Audio]::Mute)"
            )
            ps = (
                "Add-Type -TypeDefinition @'\n"
                "using System.Runtime.InteropServices;\n"
                '[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n'
                "interface IAudioEndpointVolume {\n"
                "  int _0(); int _1(); int _2(); int _3(); int _4(); int _5(); int _6();\n"
                "  int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);\n"
                "  int GetMasterVolumeLevelScalar(out float pfLevel);\n"
                "  int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, System.Guid pguidEventContext);\n"
                "  int GetMute(out bool pbMute);\n"
                "}\n"
                '[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n'
                "interface IMMDevice { int Activate(ref System.Guid iid, int dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface); }\n"
                '[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n'
                "interface IMMDeviceEnumerator { int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppDevice); }\n"
                '[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject { }\n'
                "public class Audio {\n"
                "  static IAudioEndpointVolume GetVol() {\n"
                "    var enumerator = new MMDeviceEnumeratorComObject() as IMMDeviceEnumerator;\n"
                "    IMMDevice dev; enumerator.GetDefaultAudioEndpoint(0, 1, out dev);\n"
                "    var iid = typeof(IAudioEndpointVolume).GUID; object o;\n"
                "    dev.Activate(ref iid, 1, IntPtr.Zero, out o);\n"
                "    return (IAudioEndpointVolume)o;\n"
                "  }\n"
                "  public static bool Mute { get { bool m; GetVol().GetMute(out m); return m; } set { GetVol().SetMute(value, System.Guid.Empty); } }\n"
                "}\n"
                f"'@ -ErrorAction SilentlyContinue\n[Audio]::Mute = {mute_val}"
            )
            result, status = self.execute_command(ps, timeout=10)
            if status != 0:
                return f"Error: {result}"
            return f"Volume {action}d."

        return f"Error: Unknown action: {action}"

    def brightness_control(self, action: str, level: int | None = None) -> str:
        """Control display brightness via WMI."""
        if action == "get":
            ps = "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | Select-Object -First 1).CurrentBrightness"
            result, status = self.execute_command(ps, timeout=10)
            if status != 0:
                return "Error: Cannot read brightness (may not be supported on desktop monitors)."
            return f"Display brightness: {result.strip()}%"

        if action == "set":
            if level is None:
                return "Error: level is required for 'set' action"
            if level < 0 or level > 100:
                return "Error: level must be 0-100"
            ps = f"Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | ForEach-Object {{ $_.WmiSetBrightness(1, {level}) }}"
            result, status = self.execute_command(ps, timeout=10)
            if status != 0:
                return f"Error: Cannot set brightness (may not be supported on desktop monitors). {result}"
            return f"Brightness set to {level}%"

        return f"Error: Unknown action: {action}"

    def app_list(self) -> str:
        """List all running GUI applications with window titles."""
        ps = "Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | Select-Object Id, ProcessName, MainWindowTitle | Format-Table -AutoSize | Out-String -Width 200"
        result, status = self.execute_command(ps, timeout=10)
        if status != 0:
            return f"Error: {result}"
        return f"Running applications:\n{result.strip()}"

    def app_is_running(self, name: str) -> str:
        """Check if an application is running by process name."""
        # Strip .exe extension if provided — Get-Process expects name without extension
        clean_name = name.removesuffix(".exe").removesuffix(".EXE")
        safe_name = ps_quote(clean_name)
        ps = f"if (Get-Process -Name {safe_name} -ErrorAction SilentlyContinue) {{ 'Running' }} else {{ 'Not running' }}"
        result, status = self.execute_command(ps, timeout=5)
        if status != 0:
            return f"Error: {result}"
        return f'"{name}" is {result.strip().lower()}.'

    def show_dialog(
        self,
        dialog_type: str,
        message: str | None = None,
        title: str | None = None,
        default_answer: str | None = None,
        choices: list[str] | None = None,
    ) -> str:
        """Show a Windows dialog via PowerShell."""
        safe_msg = ps_quote(message or "Please respond")
        safe_title = ps_quote(title or "Dialog")

        if dialog_type == "alert":
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms\n"
                f"[System.Windows.Forms.MessageBox]::Show({safe_msg}, {safe_title}, "
                "'OKCancel', 'Information')"
            )
            result, status = self.execute_command(ps, timeout=120)
            if status != 0:
                return f"Error: {result}"
            return f"Dialog result: {result.strip()}"

        if dialog_type == "prompt":
            safe_default = ps_quote(default_answer or "")
            ps = (
                "Add-Type -AssemblyName Microsoft.VisualBasic\n"
                f"[Microsoft.VisualBasic.Interaction]::InputBox({safe_msg}, {safe_title}, {safe_default})"
            )
            result, status = self.execute_command(ps, timeout=120)
            if status != 0:
                return f"Error: {result}"
            text = result.strip()
            if not text:
                return "User canceled the prompt (or submitted empty text)."
            return f"User entered: {text}"

        if dialog_type == "choose":
            if not choices:
                return "Error: choices list is required for 'choose' type"
            items_str = ", ".join(ps_quote(c) for c in choices)
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms\n"
                f"$form = New-Object System.Windows.Forms.Form -Property @{{Text={safe_title}; Width=350; Height=200; StartPosition='CenterScreen'; TopMost=$true}}\n"
                f"$combo = New-Object System.Windows.Forms.ComboBox -Property @{{Left=10; Top=50; Width=310; DropDownStyle='DropDownList'}}\n"
                f"@({items_str}) | ForEach-Object {{ $combo.Items.Add($_) | Out-Null }}\n"
                "$combo.SelectedIndex = 0\n"
                f"$label = New-Object System.Windows.Forms.Label -Property @{{Text={safe_msg}; Left=10; Top=10; Width=310; Height=30}}\n"
                "$ok = New-Object System.Windows.Forms.Button -Property @{Text='OK'; Left=120; Top=120; Width=80; DialogResult='OK'}\n"
                "$form.Controls.AddRange(@($label, $combo, $ok))\n"
                "$form.AcceptButton = $ok\n"
                "if ($form.ShowDialog() -eq 'OK') { $combo.SelectedItem } else { 'CANCELED' }"
            )
            result, status = self.execute_command(ps, timeout=120)
            if status != 0:
                return f"Error: {result}"
            text = result.strip()
            if text == "CANCELED":
                return "User canceled the selection."
            return f"Selected: {text}"

        if dialog_type == "fileChoose":
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms\n"
                "$d = New-Object System.Windows.Forms.OpenFileDialog -Property @{Title="
                + safe_title
                + "}\n"
                "if ($d.ShowDialog() -eq 'OK') { $d.FileName } else { 'CANCELED' }"
            )
            result, status = self.execute_command(ps, timeout=120)
            if status != 0:
                return f"Error: {result}"
            text = result.strip()
            if text == "CANCELED":
                return "User canceled file selection."
            return f"Selected file: {text}"

        return f"Error: Unknown dialog type: {dialog_type}"

    def system_info_extended(self) -> str:
        """Get extended system information via PowerShell and WMI."""
        ps = (
            "$info = @()\n"
            "$os = Get-CimInstance Win32_OperatingSystem\n"
            '$info += "Windows: $($os.Caption) $($os.Version) (Build $($os.BuildNumber))"\n'
            '$info += "Computer: $($env:COMPUTERNAME)"\n'
            '$info += "User: $($env:USERNAME)"\n'
            "$uptime = (Get-Date) - $os.LastBootUpTime\n"
            '$info += "Uptime: $($uptime.Days)d $($uptime.Hours)h $($uptime.Minutes)m"\n'
            "try {\n"
            "  $bat = Get-CimInstance Win32_Battery -ErrorAction Stop\n"
            "  $charging = if ($bat.BatteryStatus -eq 2) { '(charging)' } else { '(battery)' }\n"
            '  $info += "Battery: $($bat.EstimatedChargeRemaining)% $charging"\n'
            "} catch { $info += 'Battery: N/A (desktop)' }\n"
            "try {\n"
            "  $theme = Get-ItemPropertyValue -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' -Name 'AppsUseLightTheme' -ErrorAction Stop\n"
            "  $info += \"Dark mode: $(if ($theme -eq 0) { 'on' } else { 'off' })\"\n"
            "} catch { $info += 'Dark mode: unknown' }\n"
            "try {\n"
            "  $wifi = (Get-NetConnectionProfile -ErrorAction Stop | Where-Object { $_.InterfaceAlias -like '*Wi-Fi*' }).Name\n"
            "  if ($wifi) { $info += \"WiFi: $wifi\" } else { $info += 'WiFi: not connected' }\n"
            "} catch { $info += 'WiFi: not available' }\n"
            '$info -join "`n"'
        )
        result, status = self.execute_command(ps, timeout=15)
        if status != 0:
            return f"Error: {result}"
        return f"System Information:\n{result.strip()}"

    def dark_mode_control(self, action: str) -> str:
        """Control Windows dark/light mode via registry."""
        reg_path = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

        if action == "get":
            ps = f"Get-ItemPropertyValue -Path '{reg_path}' -Name 'AppsUseLightTheme'"
            result, status = self.execute_command(ps, timeout=5)
            if status != 0:
                return f"Error: {result}"
            is_dark = result.strip() == "0"
            return f"Dark mode is {'enabled' if is_dark else 'disabled'}."

        if action in ("enable", "disable", "toggle"):
            if action == "toggle":
                ps_get = f"Get-ItemPropertyValue -Path '{reg_path}' -Name 'AppsUseLightTheme'"
                result, status = self.execute_command(ps_get, timeout=5)
                if status != 0:
                    return f"Error: {result}"
                new_val = 1 if result.strip() == "0" else 0
            else:
                new_val = 0 if action == "enable" else 1

            ps = (
                f"Set-ItemProperty -Path '{reg_path}' -Name 'AppsUseLightTheme' -Value {new_val} -Type DWord\n"
                f"Set-ItemProperty -Path '{reg_path}' -Name 'SystemUsesLightTheme' -Value {new_val} -Type DWord"
            )
            result, status = self.execute_command(ps, timeout=5)
            if status != 0:
                return f"Error: {result}"
            mode = "enabled" if new_val == 0 else "disabled"
            return f"Dark mode {mode}."

        return f"Error: Unknown action: {action}"

    def say_text(self, text: str, voice: str | None = None, rate: int | None = None) -> str:
        """Text-to-speech via PowerShell SAPI."""
        safe_text = ps_quote(text)
        ps = "Add-Type -AssemblyName System.Speech\n$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        if voice:
            safe_voice = ps_quote(voice)
            ps += f"try {{ $s.SelectVoice({safe_voice}) }} catch {{ Write-Error ('Voice not found: ' + {safe_voice}) }}\n"
        if rate is not None:
            clamped = max(-10, min(10, rate))
            ps += f"$s.Rate = {clamped}\n"
        ps += f"$s.SpeakAsync({safe_text}) | Out-Null\nwhile ($s.State -ne 'Ready') {{ Start-Sleep -Milliseconds 100 }}\nWrite-Output 'OK'"
        result, status = self.execute_command(ps, timeout=60)
        if status != 0:
            return f"Error: {result}"
        return f"Spoke {len(text)} characters{f' with voice {voice}' if voice else ''}{f' at rate {rate}' if rate else ''}."

    def port_check(self, action: str, port: int | None = None, protocol: str = "tcp") -> str:
        """Check port usage via PowerShell Get-NetTCPConnection."""
        if action == "check":
            if port is None:
                return "Error: port is required for 'check' action"
            if protocol in ("tcp", "both"):
                ps = f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object LocalPort, RemoteAddress, State, OwningProcess | Format-Table -AutoSize | Out-String"
                result, status = self.execute_command(ps, timeout=10)
                tcp_info = result.strip() if status == 0 and result.strip() else ""
            else:
                tcp_info = ""

            if protocol in ("udp", "both"):
                ps = f"Get-NetUDPEndpoint -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object LocalPort, OwningProcess | Format-Table -AutoSize | Out-String"
                result, status = self.execute_command(ps, timeout=10)
                udp_info = result.strip() if status == 0 and result.strip() else ""
            else:
                udp_info = ""

            if tcp_info or udp_info:
                parts = []
                if tcp_info:
                    parts.append(f"TCP:\n{tcp_info}")
                if udp_info:
                    parts.append(f"UDP:\n{udp_info}")
                return f"Port {port} is IN USE:\n" + "\n".join(parts)
            return f"Port {port} is free (not in use)."

        if action == "list":
            ps = "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object LocalPort, OwningProcess | Sort-Object LocalPort | Format-Table -AutoSize | Out-String -Width 200"
            result, status = self.execute_command(ps, timeout=10)
            if status != 0:
                return f"Error: {result}"
            return f"Listening ports:\n{result.strip()}"

        return f"Error: Unknown action: {action}"

    def file_watcher(
        self,
        path: str,
        timeout_seconds: int = 30,
        event: str = "any",
    ) -> str:
        """Watch a file for changes by polling stat."""
        resolved = pathlib.Path(path).resolve()
        watch_target = resolved.parent if event == "create" and not resolved.exists() else resolved

        if not watch_target.exists():
            return f"Error: Path does not exist: {watch_target}"

        def get_state(p: pathlib.Path):
            try:
                stat = p.stat()
                return {"exists": True, "mtime": stat.st_mtime, "size": stat.st_size}
            except (FileNotFoundError, OSError):
                return {"exists": False, "mtime": 0, "size": 0}

        last_state = get_state(resolved)
        start = time()
        saw_delete = False

        while (time() - start) < timeout_seconds:
            sleep(0.25)
            current = get_state(resolved)

            if not current["exists"] and last_state["exists"]:
                saw_delete = True

            changed = False
            change_type = ""

            if event in ("create", "any"):
                if (not last_state["exists"] or saw_delete) and current["exists"]:
                    changed = True
                    change_type = "created"
                    saw_delete = False

            if event in ("delete", "any") and not changed:
                if last_state["exists"] and not current["exists"]:
                    changed = True
                    change_type = "deleted"

            if event in ("modify", "any") and not changed:
                if (
                    current["exists"]
                    and last_state["exists"]
                    and (
                        current["mtime"] != last_state["mtime"]
                        or current["size"] != last_state["size"]
                    )
                ):
                    changed = True
                    change_type = "modified"

            if changed:
                elapsed = round(time() - start, 1)
                return f"File {change_type}: {resolved} (detected in {elapsed}s). Size: {current['size']} bytes."

            last_state = current

        return f"Timeout after {timeout_seconds}s — no {event} changes detected on: {resolved}"

    def search_files(
        self,
        query: str,
        search_type: str = "name",
        directory: str | None = None,
        max_results: int = 20,
    ) -> str:
        """Search for files using PowerShell Get-ChildItem or Windows Search."""
        if search_type == "name":
            # Escape filesystem wildcard special chars before wrapping
            sanitized = query.replace("[", "`[").replace("]", "`]")
            safe_query = ps_quote(f"*{sanitized}*")
            search_dir = (
                ps_quote(str(pathlib.Path(directory).resolve()))
                if directory
                else '"$env:USERPROFILE"'
            )
            ps = f"Get-ChildItem -Path {search_dir} -Recurse -Filter {safe_query} -ErrorAction SilentlyContinue | Select-Object -First {max_results} -ExpandProperty FullName"
        elif search_type == "content":
            safe_query = ps_quote(query)
            search_dir = (
                ps_quote(str(pathlib.Path(directory).resolve()))
                if directory
                else '"$env:USERPROFILE"'
            )
            ps = f"Get-ChildItem -Path {search_dir} -Recurse -File -ErrorAction SilentlyContinue | Select-String -Pattern {safe_query} -SimpleMatch -List -ErrorAction SilentlyContinue | Select-Object -First {max_results} -ExpandProperty Path"
        else:
            return f"Error: Unknown search_type: {search_type}"

        result, status = self.execute_command(ps, timeout=30)
        if status != 0:
            return f"Error: {result}"
        results = result.strip()
        if not results:
            return f'No results found for "{query}".'
        lines = results.split("\n")
        return f"Found {len(lines)} result(s):\n{results}"

    def network_diagnostics(
        self,
        action: str,
        host: str | None = None,
        count: int = 3,
        timeout: int = 5,
    ) -> str:
        """Network diagnostic utilities via PowerShell."""
        if action == "ping":
            if not host:
                return "Error: host is required for ping"
            safe_host = ps_quote(host)
            ps = f"Test-Connection -ComputerName {safe_host} -Count {count} -TimeoutSeconds {timeout} | Format-Table -AutoSize | Out-String -Width 200"
            result, status = self.execute_command(ps, timeout=timeout + 10)
            if status != 0:
                return f"Ping {host} failed: {result}"
            return f"Ping {host}:\n{result.strip()}"

        if action == "dns":
            if not host:
                return "Error: host is required for dns"
            safe_host = ps_quote(host)
            ps = f"Resolve-DnsName {safe_host} -ErrorAction Stop | Format-Table -AutoSize | Out-String -Width 200"
            result, status = self.execute_command(ps, timeout=timeout + 5)
            if status != 0:
                return f"DNS lookup failed for {host}: {result}"
            return f"DNS lookup {host}:\n{result.strip()}"

        if action == "http":
            if not host:
                return "Error: host is required for http"
            url = host if host.startswith("http") else f"https://{host}"
            safe_url = ps_quote(url)
            ps = (
                f"$r = Invoke-WebRequest -Uri {safe_url} -UseBasicParsing -TimeoutSec {timeout} -Method GET\n"
                '"HTTP $($r.StatusCode) | Content-Length: $($r.RawContentLength) bytes"'
            )
            result, status = self.execute_command(ps, timeout=timeout + 10)
            if status != 0:
                return f"HTTP check {url} failed: {result}"
            return f"HTTP check {url}:\n{result.strip()}"

        if action == "interfaces":
            ps = "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } | Select-Object InterfaceAlias, IPAddress | Format-Table -AutoSize | Out-String"
            result, status = self.execute_command(ps, timeout=10)
            if status != 0:
                return f"Error: {result}"
            return f"Network interfaces:\n{result.strip()}"

        return f"Error: Unknown action: {action}"

    def accessibility_inspector(
        self,
        app_name: str,
        max_depth: int = 3,
    ) -> str:
        """Read UI element tree using UIAutomation library."""
        try:
            # Find the app window
            windows = uia.WindowControl(searchDepth=1, Name=app_name)
            if not windows.Exists(maxSearchSeconds=3):
                # Try partial match
                all_windows = uia.GetRootControl().GetChildren()
                target = None
                for w in all_windows:
                    if app_name.lower() in (w.Name or "").lower():
                        target = w
                        break
                if not target:
                    return f'No window found matching "{app_name}".'
                windows = target

            lines = [f"Window: {windows.Name} [{windows.ControlTypeName}]"]

            def walk(element, depth, max_d):
                if depth >= max_d:
                    return
                try:
                    children = element.GetChildren()
                except Exception:
                    return
                for child in children:
                    indent = "  " * (depth + 1)
                    name = child.Name or ""
                    role = child.ControlTypeName or ""
                    val = ""
                    try:
                        val = (
                            child.GetValuePattern().Value
                            if hasattr(child, "GetValuePattern")
                            else ""
                        )
                    except Exception:
                        pass
                    enabled = child.IsEnabled
                    line = f"{indent}[{role}] {name}"
                    if val and val != name:
                        line += f" = {val}"
                    if not enabled:
                        line += " (disabled)"
                    lines.append(line)
                    walk(child, depth + 1, max_d)

            walk(windows, 0, max_depth)
            return "\n".join(lines[:500])  # Cap at 500 lines

        except Exception as e:
            return f"Error: Accessibility inspection failed: {str(e)}"

    # ============== UI ELEMENT OPERATIONS ==============

    def _find_app_window(self, app_name: str) -> "uia.Control | None":
        """Find a window by exact or partial name match. Returns None if not found."""
        window = uia.WindowControl(searchDepth=1, Name=app_name)
        if window.Exists(maxSearchSeconds=2):
            return window
        # Partial match fallback
        all_windows = uia.GetRootControl().GetChildren()
        for w in all_windows:
            if app_name.lower() in (w.Name or "").lower():
                return w
        return None

    def _navigate_to_element(self, root: "uia.Control", path: str) -> "uia.Control | None":
        """Navigate to element by path like 'pane 2 > button 3'.

        Path segments: 'role index' where index is 1-based.
        """
        import re as _re

        current = root
        segments = [s.strip() for s in path.split(">")]
        for seg in segments:
            match = _re.match(r"^(\w+)\s*(\d+)?$", seg.strip())
            if not match:
                return None
            role_name = match.group(1).lower()
            index = int(match.group(2) or "1")
            children = current.GetChildren()
            count = 0
            found = False
            for child in children:
                child_role = (child.ControlTypeName or "").lower()
                if child_role == role_name:
                    count += 1
                    if count == index:
                        current = child
                        found = True
                        break
            if not found:
                return None
        return current

    def _search_element(
        self,
        root: "uia.Control",
        search: str,
        role: str | None = None,
        max_depth: int = 5,
    ) -> "uia.Control | None":
        """Search for element by name (fuzzy) and optional role filter."""

        def walk(el, depth):
            if depth > max_depth:
                return None
            try:
                children = el.GetChildren()
            except Exception:
                return None
            for child in children:
                name = (child.Name or "").strip()
                child_role = (child.ControlTypeName or "").lower()
                if role and child_role != role.lower():
                    # Role mismatch: still recurse deeper but skip name check
                    result = walk(child, depth + 1)
                    if result:
                        return result
                    continue
                if search.lower() in name.lower():
                    return child
                result = walk(child, depth + 1)
                if result:
                    return result
            return None

        return walk(root, 0)

    def ui_element_get(self, app_name: str, depth: int = 1, role: str | None = None) -> str:
        """Get UI element tree for an application with depth and role filtering."""
        window = self._find_app_window(app_name)
        if not window:
            return f'No window found matching "{app_name}".'

        lines = [f"Window: {window.Name} [{window.ControlTypeName}]"]

        def walk(element, d, max_d, idx_path):
            if d >= max_d:
                return
            try:
                children = element.GetChildren()
            except Exception:
                return
            role_counts: dict[str, int] = {}
            for child in children:
                child_role = (child.ControlTypeName or "").lower()
                role_counts[child_role] = role_counts.get(child_role, 0) + 1
                child_index = role_counts[child_role]

                if role and child_role != role.lower():
                    continue

                indent = "  " * (d + 1)
                name = (child.Name or "").replace("\n", " ").replace("\r", "")
                path_str = (
                    f"{idx_path} > {child_role} {child_index}"
                    if idx_path
                    else f"{child_role} {child_index}"
                )
                val = ""
                try:
                    val = child.GetValuePattern().Value
                except Exception:
                    pass
                enabled = child.IsEnabled
                rect = child.BoundingRectangle
                pos = ""
                if rect.width() > 0:
                    pos = f" @({rect.left},{rect.top},{rect.width()},{rect.height()})"
                line = f"{indent}[{child_role}] {name}"
                if val and val != name:
                    line += f" = {val}"
                if not enabled:
                    line += " (disabled)"
                line += pos
                line += f"  path: {path_str}"
                lines.append(line)
                walk(child, d + 1, max_d, path_str)

        walk(window, 0, depth, "")
        return "\n".join(lines[:500])

    def ui_element_find(self, app_name: str, search: str, role: str | None = None) -> str:
        """Find a specific UI element by name search."""
        window = self._find_app_window(app_name)
        if not window:
            return f'No window found matching "{app_name}".'

        element = self._search_element(window, search, role)
        if not element:
            return f'No element found matching "{search}"{f" with role {role}" if role else ""}.'

        name = (element.Name or "").replace("\n", " ")
        el_role = element.ControlTypeName or ""
        enabled = element.IsEnabled
        rect = element.BoundingRectangle
        val = ""
        try:
            val = element.GetValuePattern().Value
        except Exception:
            pass

        result = f"Found: [{el_role}] {name}"
        if val:
            result += f" = {val}"
        if not enabled:
            result += " (disabled)"
        if rect.width() > 0:
            result += f" @({rect.left},{rect.top},{rect.width()},{rect.height()})"
        return result

    def ui_element_click(
        self, app_name: str, path: str | None = None, search: str | None = None
    ) -> str:
        """Click a UI element by path or search."""
        window = self._find_app_window(app_name)
        if not window:
            return f'No window found matching "{app_name}".'

        element = None
        if path:
            element = self._navigate_to_element(window, path)
        elif search:
            element = self._search_element(window, search)

        if not element:
            target = path or search
            return f'Element not found: "{target}".'

        name = (element.Name or "").replace("\n", " ")
        el_role = element.ControlTypeName or ""

        # Try InvokePattern first
        try:
            element.GetInvokePattern().Invoke()
            return f"Clicked [{el_role}] {name} via InvokePattern."
        except Exception:
            pass

        # Try ExpandCollapsePattern
        try:
            pattern = element.GetExpandCollapsePattern()
            state = pattern.ExpandCollapseState
            if state == 0:  # Collapsed
                pattern.Expand()
            else:
                pattern.Collapse()
            return f"Toggled [{el_role}] {name} via ExpandCollapsePattern."
        except Exception:
            pass

        # Fallback: click at center of bounds
        try:
            rect = element.BoundingRectangle
            if rect.width() > 0:
                cx = rect.left + rect.width() // 2
                cy = rect.top + rect.height() // 2
                self.click((cx, cy), button="left", clicks=1)
                return f"Clicked [{el_role}] {name} at ({cx}, {cy})."
        except Exception:
            pass

        return f"Failed to click [{el_role}] {name}: no supported interaction pattern."

    def ui_element_set_value(
        self,
        app_name: str,
        value: str,
        path: str | None = None,
        search: str | None = None,
    ) -> str:
        """Set value on a UI element (text field, checkbox, etc.)."""
        window = self._find_app_window(app_name)
        if not window:
            return f'No window found matching "{app_name}".'

        element = None
        if path:
            element = self._navigate_to_element(window, path)
        elif search:
            element = self._search_element(window, search)

        if not element:
            target = path or search
            return f'Element not found: "{target}".'

        name = (element.Name or "").replace("\n", " ")
        el_role = element.ControlTypeName or ""

        # Try ValuePattern (text fields, combo boxes)
        try:
            element.GetValuePattern().SetValue(value)
            return f"Set [{el_role}] {name} = {value} via ValuePattern."
        except Exception:
            pass

        # Try TogglePattern (checkboxes)
        try:
            toggle = element.GetTogglePattern()
            target_on = value.lower() in ("true", "on", "1", "yes", "checked")
            current = toggle.ToggleState
            if (target_on and current != 1) or (not target_on and current == 1):
                toggle.Toggle()
            return f"Toggled [{el_role}] {name} to {value} via TogglePattern."
        except Exception:
            pass

        # Try SelectionItemPattern (radio buttons, list items)
        try:
            element.GetSelectionItemPattern().Select()
            return f"Selected [{el_role}] {name} via SelectionItemPattern."
        except Exception:
            pass

        # Try RangeValuePattern (sliders, spinners)
        try:
            rv = element.GetRangeValuePattern()
            rv.SetValue(float(value))
            return f"Set [{el_role}] {name} = {value} via RangeValuePattern."
        except Exception:
            pass

        return f"Failed to set value on [{el_role}] {name}: no supported value pattern."

    def ui_element_type_into(
        self,
        app_name: str,
        text: str,
        path: str | None = None,
        search: str | None = None,
        clear: bool = False,
    ) -> str:
        """Type text into a UI element by focusing it first."""
        window = self._find_app_window(app_name)
        if not window:
            return f'No window found matching "{app_name}".'

        element = None
        if path:
            element = self._navigate_to_element(window, path)
        elif search:
            element = self._search_element(window, search)

        if not element:
            target = path or search
            return f'Element not found: "{target}".'

        name = (element.Name or "").replace("\n", " ")
        el_role = element.ControlTypeName or ""

        try:
            element.SetFocus()
            sleep(0.1)
        except Exception:
            pass

        if clear:
            # Select all then delete
            uia.SendKeys("{Ctrl}a", waitTime=0.05)
            uia.SendKeys("{Delete}", waitTime=0.05)

        escaped = _escape_text_for_sendkeys(text)
        uia.SendKeys(escaped, waitTime=0.05)
        return f"Typed {len(text)} chars into [{el_role}] {name}."

    def ui_element_list_windows(self) -> str:
        """List all visible windows with details."""
        ps = (
            "Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | "
            "ForEach-Object { "
            "  $h = $_.MainWindowHandle; "
            "  $r = New-Object 'System.Drawing.Rectangle'; "
            "  try { "
            "    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue; "
            "  } catch {} "
            '  "$($_.Id)|$($_.ProcessName)|$($_.MainWindowTitle)|$h" '
            "} | Out-String -Width 500"
        )
        result, status = self.execute_command(ps, timeout=10)
        if status != 0:
            return f"Error: {result}"

        lines = ["PID | Process | Title | Handle"]
        lines.append("-" * 60)
        for line in result.strip().split("\n"):
            line = line.strip()
            if line:
                lines.append(line.replace("|", " | "))
        return "\n".join(lines)

    def ui_element_overview(self, app_name: str) -> str:
        """Get element role counts for an application."""
        window = self._find_app_window(app_name)
        if not window:
            return f'No window found matching "{app_name}".'

        role_counts: dict[str, int] = {}
        total = 0

        def count_roles(element, depth, max_depth=4):
            nonlocal total
            if depth >= max_depth:
                return
            try:
                children = element.GetChildren()
            except Exception:
                return
            for child in children:
                role = child.ControlTypeName or "Unknown"
                role_counts[role] = role_counts.get(role, 0) + 1
                total += 1
                count_roles(child, depth + 1, max_depth)

        count_roles(window, 0)

        lines = [f"App Overview: {window.Name}", f"Total elements: {total}", ""]
        for role, count in sorted(role_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {role}: {count}")
        return "\n".join(lines)

    # ============== WINDOW SCREENSHOT ==============

    def capture_window_screenshot(
        self, app_name: str | None = None, handle: int | None = None
    ) -> "Image.Image | None":
        """Capture screenshot of a specific window."""
        if handle:
            try:
                rect_tuple = win32gui.GetWindowRect(handle)
                # rect_tuple is (left, top, right, bottom)
                bbox = (rect_tuple[0], rect_tuple[1], rect_tuple[2], rect_tuple[3])
                img = ImageGrab.grab(bbox=bbox)
                return img
            except Exception as e:
                logger.error(f"Screenshot by handle failed: {e}")
                return None

        if app_name:
            window = self._find_app_window(app_name)
            if not window:
                return None
            try:
                hwnd = window.NativeWindowHandle
                rect_tuple = win32gui.GetWindowRect(hwnd)
                bbox = (rect_tuple[0], rect_tuple[1], rect_tuple[2], rect_tuple[3])
                img = ImageGrab.grab(bbox=bbox)
                return img
            except Exception as e:
                logger.error(f"Screenshot by app name failed: {e}")
                return None

        return None

    # ============== MULTI MONITOR ==============

    def get_multi_monitor_info(self) -> str:
        """Get information about all connected monitors."""
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms\n"
            "[System.Windows.Forms.Screen]::AllScreens | ForEach-Object {\n"
            "  $b = $_.Bounds\n"
            "  $w = $_.WorkingArea\n"
            '  "Name: $($_.DeviceName) | Primary: $($_.Primary) | "\n'
            '  + "Bounds: $($b.X),$($b.Y) $($b.Width)x$($b.Height) | "\n'
            '  + "WorkArea: $($w.X),$($w.Y) $($w.Width)x$($w.Height) | "\n'
            '  + "BPP: $($_.BitsPerPixel)"\n'
            "}"
        )
        result, status = self.execute_command(ps, timeout=10)
        if status != 0:
            return f"Error: {result}"
        return f"Monitors:\n{result.strip()}"

    # ============== SCREEN RECORDING ==============

    def screen_record(
        self,
        action: str,
        output_path: str | None = None,
        duration: int | None = None,
        fps: int = 15,
    ) -> str:
        """Control screen recording using ffmpeg."""
        if not shutil.which("ffmpeg"):
            return "Error: ffmpeg not found in PATH. Install ffmpeg first."

        state_file = os.path.join(tempfile.gettempdir(), "wmcp_screen_record.pid")

        if action == "start":
            # Validate output_path to prevent path traversal / ffmpeg option injection
            if output_path:
                resolved_out = pathlib.Path(output_path).resolve()
                if resolved_out.suffix.lower() not in {".mp4", ".mkv", ".avi"}:
                    return "Error: output_path must have .mp4, .mkv, or .avi extension"
                if str(resolved_out).startswith("-"):
                    return "Error: output_path must not start with '-'"
                out = str(resolved_out)
            else:
                out = os.path.join(
                    os.path.expanduser("~"),
                    "Desktop",
                    f"recording_{int(time())}.mp4",
                )

            # Atomic check-and-create to prevent TOCTOU race
            try:
                fd = os.open(state_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                return "Error: Recording already in progress. Stop it first."

            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "gdigrab",
                "-framerate",
                str(fps),
                "-i",
                "desktop",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
            ]
            if duration:
                cmd += ["-t", str(duration)]
            cmd.append(out)

            # Use CREATE_NEW_PROCESS_GROUP so we can send CTRL_BREAK_EVENT
            # to gracefully stop ffmpeg (allows it to finalize the video file)
            create_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=create_flags,
            )
            os.write(fd, f"{proc.pid}\n{out}".encode())
            os.close(fd)
            return f"Recording started (PID {proc.pid}). Output: {out}"

        if action == "stop":
            if not os.path.exists(state_file):
                return "No recording in progress."
            with open(state_file, "r") as f:
                lines = f.read().strip().split("\n")
            pid = int(lines[0])
            out = lines[1] if len(lines) > 1 else "unknown"
            try:
                # Verify the PID is actually ffmpeg before sending signal
                p = Process(pid)
                if "ffmpeg" not in p.name().lower():
                    try:
                        os.remove(state_file)
                    except OSError:
                        pass
                    return f"PID {pid} is not ffmpeg (is {p.name()}). State file cleaned up."
            except Exception:
                try:
                    os.remove(state_file)
                except OSError:
                    pass
                return "Recording process not found. State file cleaned up."
            try:
                import signal

                # Send CTRL_BREAK_EVENT for graceful ffmpeg shutdown
                # (allows finalization of the MP4 container)
                os.kill(pid, getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT))
            except (OSError, ProcessLookupError):
                pass
            try:
                os.remove(state_file)
            except OSError:
                pass
            return f"Recording stopped. Output: {out}"

        if action == "status":
            if not os.path.exists(state_file):
                return "No recording in progress."
            with open(state_file, "r") as f:
                lines = f.read().strip().split("\n")
            pid = int(lines[0])
            out = lines[1] if len(lines) > 1 else "unknown"
            try:
                p = Process(pid)
                if "ffmpeg" not in p.name().lower():
                    try:
                        os.remove(state_file)
                    except OSError:
                        pass
                    return "Recording process not found (PID recycled). State file cleaned up."
                return f"Recording in progress (PID {pid}). Output: {out}"
            except Exception:
                try:
                    os.remove(state_file)
                except OSError:
                    pass
                return "Recording process not found (may have finished)."

        return f"Error: Unknown action: {action}"

    # ============== MENU CLICK ==============

    def menu_click(self, app_name: str, menu_path: str) -> str:
        """Navigate and click menu items by path (e.g., 'File > Save As')."""
        window = self._find_app_window(app_name)
        if not window:
            return f'No window found matching "{app_name}".'

        try:
            window.SetFocus()
            sleep(0.2)
        except Exception:
            pass

        segments = [s.strip() for s in menu_path.split(">")]
        current = window

        for i, menu_name in enumerate(segments):
            # Search for menu item
            found = None
            try:
                children = current.GetChildren()
                for child in children:
                    child_role = (child.ControlTypeName or "").lower()
                    child_name = (child.Name or "").strip()
                    if child_role in ("menubar", "menu", "menuitem"):
                        if menu_name.lower() in child_name.lower():
                            found = child
                            break
                        # Check children of menu bar
                        if child_role == "menubar":
                            bar_children = child.GetChildren()
                            for bar_child in bar_children:
                                bar_name = (bar_child.Name or "").strip()
                                if menu_name.lower() in bar_name.lower():
                                    found = bar_child
                                    break
                            if found:
                                break
            except Exception:
                pass

            if not found:
                return f'Menu item "{menu_name}" not found at level {i + 1}.'

            # Click/expand the menu item
            try:
                found.GetInvokePattern().Invoke()
                sleep(0.3)
            except Exception:
                try:
                    found.GetExpandCollapsePattern().Expand()
                    sleep(0.3)
                except Exception:
                    try:
                        rect = found.BoundingRectangle
                        if rect.width() > 0:
                            cx = rect.left + rect.width() // 2
                            cy = rect.top + rect.height() // 2
                            self.click((cx, cy), button="left", clicks=1)
                            sleep(0.3)
                    except Exception:
                        return f'Failed to activate menu item "{menu_name}".'

            current = found

        return f"Clicked menu path: {menu_path}"

    # ============== QUICK LOOK ==============

    def quick_look(self, path: str) -> str:
        """Open a file with its default application."""
        resolved = pathlib.Path(path).resolve()
        if not resolved.exists():
            return f"Error: File not found: {resolved}"
        try:
            os.startfile(str(resolved))
            return f"Opened: {resolved}"
        except Exception as e:
            return f"Error opening file: {e}"

    # ============== WINDOW TILING ==============

    def window_tiling(self, mode: str, app_name: str | None = None) -> str:
        """Arrange windows in various tiling layouts."""
        SWP_SHOWWINDOW = 0x0040

        if mode in ("maximize", "restore", "minimize"):
            if not app_name:
                return "Error: app_name is required for maximize/restore/minimize"
            window = self._find_app_window(app_name)
            if not window:
                return f'No window found matching "{app_name}".'
            hwnd = window.NativeWindowHandle
            if mode == "maximize":
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            elif mode == "minimize":
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            elif mode == "restore":
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            return f"Window {app_name} {mode}d."

        if mode in ("left", "right", "top", "bottom"):
            if not app_name:
                return "Error: app_name is required for tiling"
            window = self._find_app_window(app_name)
            if not window:
                return f'No window found matching "{app_name}".'
            hwnd = window.NativeWindowHandle
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            # Get work area
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms\n"
                "$w = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea\n"
                '"$($w.X),$($w.Y),$($w.Width),$($w.Height)"'
            )
            result, status = self.execute_command(ps, timeout=5)
            if status != 0:
                return f"Error getting screen info: {result}"
            parts = result.strip().split(",")
            sx, sy, sw, sh = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])

            if mode == "left":
                x, y, w, h = sx, sy, sw // 2, sh
            elif mode == "right":
                x, y, w, h = sx + sw // 2, sy, sw // 2, sh
            elif mode == "top":
                x, y, w, h = sx, sy, sw, sh // 2
            elif mode == "bottom":
                x, y, w, h = sx, sy + sh // 2, sw, sh // 2

            ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, w, h, SWP_SHOWWINDOW)
            return f"Tiled {app_name} to {mode} half."

        if mode == "cascade":
            ps = (
                "Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | "
                "ForEach-Object { $_.MainWindowHandle } | Out-String"
            )
            result, status = self.execute_command(ps, timeout=10)
            if status != 0:
                return f"Error: {result}"
            handles = []
            for h in result.strip().split("\n"):
                h = h.strip()
                if h:
                    try:
                        handles.append(int(h))
                    except ValueError:
                        pass  # skip non-numeric lines (headers, errors)
            offset = 30
            for i, hwnd in enumerate(handles):
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    ctypes.windll.user32.SetWindowPos(
                        hwnd, 0, offset * i, offset * i, 800, 600, SWP_SHOWWINDOW
                    )
                except Exception:
                    pass
            return f"Cascaded {len(handles)} windows."

        return f"Error: Unknown tiling mode: {mode}"

    # ============== CLIPBOARD INFO ==============

    def get_clipboard_info(self) -> str:
        """Get clipboard format details."""
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms\n"
            "$d = $null\n"
            "for ($i = 0; $i -lt 3; $i++) {\n"
            "  try { $d = [System.Windows.Forms.Clipboard]::GetDataObject(); break }\n"
            "  catch { Start-Sleep -Milliseconds 100 }\n"
            "}\n"
            "if ($d -eq $null) { 'Clipboard is empty or locked' } else {\n"
            "  $formats = $d.GetFormats()\n"
            '  $info = @("Clipboard formats (" + $formats.Count + "):")\n'
            "  foreach ($f in $formats) {\n"
            "    $hasData = $d.GetDataPresent($f)\n"
            '    $info += "  $f (present: $hasData)"\n'
            "  }\n"
            "  if ($d.ContainsText()) {\n"
            "    $text = $d.GetText()\n"
            "    $preview = if ($text.Length -gt 100) { $text.Substring(0, 100) + '...' } else { $text }\n"
            '    $info += ""\n'
            '    $info += "Text preview: $preview"\n'
            '    $info += "Text length: $($text.Length) chars"\n'
            "  }\n"
            "  if ($d.ContainsImage()) {\n"
            "    $img = $d.GetImage()\n"
            '    $info += "Image: $($img.Width)x$($img.Height)"\n'
            "  }\n"
            '  $info -join "`n"\n'
            "}"
        )
        result, status = self.execute_command(ps, timeout=10)
        if status != 0:
            return f"Error: {result}"
        return result.strip()

    # ============== APP CONTROL ENHANCEMENTS ==============

    def window_control(self, app_name: str, action: str) -> str:
        """Control window state: minimize, maximize, close, fullscreen, restore."""
        window = self._find_app_window(app_name)
        if not window:
            return f'No window found matching "{app_name}".'

        hwnd = window.NativeWindowHandle

        if action == "minimize":
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return f"Minimized: {app_name}"
        elif action == "maximize":
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            return f"Maximized: {app_name}"
        elif action == "restore":
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            return f"Restored: {app_name}"
        elif action == "close":
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return f"Sent close to: {app_name}"
        elif action == "fullscreen":
            screen_size = self.get_screen_size()
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, screen_size.width, screen_size.height, 0x0040
            )
            return f"Fullscreen: {app_name}"
        else:
            return f"Error: Unknown action: {action}"
