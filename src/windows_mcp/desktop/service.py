from windows_mcp.desktop.utils import ps_quote, ps_quote_for_xml, _approximate_color_name
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
                    env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL;.PY;.PYW"

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
            validation_id = appid.replace("\\", "").replace("_", "").replace(".", "").replace("-", "").replace("!", "")
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

    def click(self, loc: tuple[int, int]|list[int], button: str = "left", clicks: int = 2):
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

    def drag(self, loc: tuple[int, int]|list[int]):
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

    def multi_select(self, press_ctrl: bool | str = False, locs: list[tuple[int, int]] = []):
        press_ctrl = press_ctrl is True or (
            isinstance(press_ctrl, str) and press_ctrl.lower() == "true"
        )
        if press_ctrl:
            uia.PressKey(uia.Keys.VK_CONTROL, waitTime=0.05)
        for loc in locs:
            x, y = loc
            uia.Click(x, y, waitTime=0.2)
            sleep(0.5)
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
        return no_children or is_name

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

        # Draw annotations in parallel
        with ThreadPoolExecutor() as executor:
            executor.map(draw_annotation, range(len(nodes)), nodes)
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
            return f'Notification may have been sent. PowerShell output: {response[:200]}'

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
            return f'Error reading registry: {response.strip()}'
        return f'Registry value [{path}] "{name}" = {response.strip()}'

    def registry_set(self, path: str, name: str, value: str, reg_type: str = 'String') -> str:
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
            return f'Error writing registry: {response.strip()}'
        return f'Registry value [{path}] "{name}" set to "{value}" (type: {reg_type}).'

    def registry_delete(self, path: str, name: str | None = None) -> str:
        q_path = ps_quote(path)
        if name:
            q_name = ps_quote(name)
            command = f"Remove-ItemProperty -Path {q_path} -Name {q_name} -Force"
            response, status = self.execute_command(command)
            if status != 0:
                return f'Error deleting registry value: {response.strip()}'
            return f'Registry value [{path}] "{name}" deleted.'
        else:
            command = f"Remove-Item -Path {q_path} -Recurse -Force"
            response, status = self.execute_command(command)
            if status != 0:
                return f'Error deleting registry key: {response.strip()}'
            return f'Registry key [{path}] deleted.'

    def registry_list(self, path: str) -> str:
        q_path = ps_quote(path)
        command = (
            f"$values = (Get-ItemProperty -Path {q_path} -ErrorAction Stop | "
            f"Select-Object * -ExcludeProperty PS* | Format-List | Out-String).Trim(); "
            f"$subkeys = (Get-ChildItem -Path {q_path} -ErrorAction SilentlyContinue | "
            f"Select-Object -ExpandProperty PSChildName) -join \"`n\"; "
            f"if ($values) {{ Write-Output \"Values:`n$values\" }}; "
            f"if ($subkeys) {{ Write-Output \"`nSub-Keys:`n$subkeys\" }}; "
            f"if (-not $values -and -not $subkeys) {{ Write-Output 'No values or sub-keys found.' }}"
        )
        response, status = self.execute_command(command)
        if status != 0:
            return f'Error listing registry: {response.strip()}'
        return f'Registry key [{path}]:\n{response.strip()}'

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
            img = ImageGrab.grab(bbox=(x, y, x + 1, y + 1))
            pixel = img.getpixel((0, 0))
            r, g, b = pixel[0], pixel[1], pixel[2]
            hex_color = f"#{r:02X}{g:02X}{b:02X}"
            name = _approximate_color_name(r, g, b)
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

        return f"Monitors ({len(lines)}):\n" + "\n".join(lines)

    def highlight_region(
        self, loc: list[int], size: list[int], duration: float = 2.0, color: str = "red"
    ) -> str:
        if len(loc) != 2:
            return "Error: loc must be [x, y]"
        if len(size) != 2:
            return "Error: size must be [width, height]"
        x, y = loc[0], loc[1]
        w, h = size[0], size[1]
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
                img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            else:
                img = ImageGrab.grab()

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
        timeout = min(timeout, 60.0)  # Hard cap at 60s
        poll_interval = max(poll_interval, 0.1)  # Prevent CPU spinning
        bbox = (x, y, x + w, y + h)

        try:
            baseline = list(ImageGrab.grab(bbox=bbox).getdata())
        except Exception as e:
            return f"Error capturing baseline: {str(e)}"

        total_pixels = len(baseline)
        if total_pixels == 0:
            return "Error: region has zero pixels."

        start = time()
        while (time() - start) < timeout:
            sleep(poll_interval)
            try:
                current = list(ImageGrab.grab(bbox=bbox).getdata())
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
                screen_img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            else:
                x, y = 0, 0
                screen_img = ImageGrab.grab()

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

