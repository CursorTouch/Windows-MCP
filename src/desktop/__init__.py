import csv
import io
import subprocess
from io import BytesIO
from time import sleep

import pyautogui
from fuzzywuzzy import process
from PIL import Image
from psutil import Process
from uiautomation import (
    Control,
    ControlType,
    GetFocusedControl,
    GetRootControl,
    GetScreenSize,
    SetWindowTopmost,
)

from src.desktop.config import BROWSER_NAMES, EXCLUDED_APPS, WINDOW_SWITCH_MODE
from src.desktop.views import App, DesktopState, Size
from src.tree import Tree


class Desktop:
    def __init__(self) -> None:
        self.desktop_state = None

    def get_state(self, use_vision: bool = False) -> DesktopState:
        tree = Tree(self)
        tree_state = tree.get_state()
        if use_vision:
            nodes = tree_state.interactive_nodes
            annotated_screenshot = (
                tree.annotated_screenshot(nodes=nodes, scale=0.5)
                if use_vision
                else None
            )
            screenshot = self.screenshot_in_bytes(screenshot=annotated_screenshot)
        else:
            screenshot = None
        apps = self.get_apps()
        active_app, apps = (apps[0], apps[1:]) if len(apps) > 0 else (None, [])
        self.desktop_state = DesktopState(
            apps=apps,
            active_app=active_app,
            screenshot=screenshot,
            tree_state=tree_state,
        )
        return self.desktop_state

    def get_taskbar(self) -> Control:
        root = GetRootControl()
        taskbar = root.GetFirstChildControl()
        return taskbar

    def get_app_status(self, control: Control) -> str:
        taskbar = self.get_taskbar()
        screen_width, screen_height = GetScreenSize()
        window = control.BoundingRectangle
        taskbar_height = taskbar.BoundingRectangle.height()
        window_width, window_height = window.width(), window.height()
        if window.isempty():
            return "Minimized"
        if (
            window_width >= screen_width
            and window_height >= screen_height - taskbar_height
        ):
            return "Maximized"
        return "Normal"

    def get_element_under_cursor(self) -> Control:
        return GetFocusedControl()

    def is_app_browser(self, node: Control) -> bool:
        process = Process(node.ProcessId)
        return process.name() in BROWSER_NAMES

    def get_apps_from_start_menu(self) -> dict[str, str]:
        command = "Get-StartApps | ConvertTo-Csv -NoTypeInformation"
        apps_info, _ = self.execute_command(command)
        reader = csv.DictReader(io.StringIO(apps_info))
        return {row.get("Name").lower(): row.get("AppID") for row in reader}

    def execute_command(self, command: str) -> tuple[str, int]:
        try:
            result = subprocess.run(
                ["powershell", "-Command"] + command.split(),
                capture_output=True,
                check=True,
            )
            return (result.stdout.decode("latin1"), result.returncode)
        except subprocess.CalledProcessError as e:
            return (e.stdout.decode("latin1"), e.returncode)

    def launch_app(self, name: str):
        apps_map = self.get_apps_from_start_menu()
        matched_app = process.extractOne(name, apps_map.keys())
        if matched_app is None:
            return (f"Application {name.title()} not found in start menu.", 1)
        app_name, _ = matched_app
        appid = apps_map.get(app_name)
        if appid is None:
            return (f"Application {name.title()} not found in start menu.", 1)
        if name.endswith(".exe"):
            response, status = self.execute_command(f'Start-Process "{appid}"')
        else:
            response, status = self.execute_command(
                f'Start-Process "shell:AppsFolder\\{appid}"'
            )
        return response, status

    def get_focused_window_handle(self) -> int:
        """Get the handle of the currently focused window"""
        try:
            focused = GetFocusedControl()
            if focused:
                # Strategy 1: Walk up the control tree to find WindowControl
                current = focused
                while current:
                    # If we found a WindowControl, this is our target window
                    if current.ControlType == ControlType.WindowControl:
                        return current.NativeWindowHandle

                    # Move up to parent, but stop if we reach desktop level
                    parent = current.GetParentControl()
                    if not parent or parent.ControlType == ControlType.PaneControl:
                        break

                    current = parent

                # Strategy 2: If no WindowControl found, look for any control with valid handle
                # but avoid the desktop handle (0x1000C / 65548)
                current = focused
                while current:
                    # Return first control with a valid handle (not 0 and not desktop)
                    if (
                        current.NativeWindowHandle != 0
                        and current.NativeWindowHandle != 0x1000C
                        and current.NativeWindowHandle != 65548
                    ):
                        return current.NativeWindowHandle

                    parent = current.GetParentControl()
                    if not parent or parent.ControlType == ControlType.PaneControl:
                        break

                    current = parent

        except Exception:
            # Catch exceptions from the focused control logic
            pass

        # Strategy 3: Use foreground window API as fallback (outside main try block)
        try:
            import win32gui

            foreground_handle = win32gui.GetForegroundWindow()
            if foreground_handle:
                return foreground_handle
        except Exception:
            # win32gui errors, continue to return 0
            pass

        return 0

    def switch_app(self, name: str) -> tuple[str, int]:
        """
        Intelligently switch to application window, handling multiple instances.
        Always refreshes desktop state to get current window information.
        """
        # CRITICAL FIX: Always refresh desktop state before switching
        self.get_state(use_vision=False)

        if not self.desktop_state or not self.desktop_state.apps:
            return ("No applications found.", 1)

        # Get current focused window to avoid switching to same window
        current_focused_handle = self.get_focused_window_handle()

        # Find all apps that match the name (fuzzy matching)
        app_candidates = []
        for app in self.desktop_state.apps:
            # Try matching against both app name and window title
            name_match = process.extractOne(name, [app.name])
            title_match = process.extractOne(name, [app.title])

            name_score = name_match[1] if name_match else 0
            title_score = title_match[1] if title_match else 0

            # Use the higher score, but require at least 60% match
            best_score = max(name_score, title_score)
            if best_score >= 60:
                app_candidates.append((app, best_score))

        if not app_candidates:
            return (f"Application '{name}' not found.", 1)

        # Sort by match score (descending)
        app_candidates.sort(key=lambda x: x[1], reverse=True)

        # Intelligent selection logic
        selected_app = None

        if len(app_candidates) == 1:
            # Only one match - use it
            selected_app = app_candidates[0][0]
        else:
            # Multiple matches - use smart selection
            # First, try to find a different window than currently focused
            for app, score in app_candidates:
                if app.handle != current_focused_handle:
                    selected_app = app
                    break

            # If all windows are the same as current focus, use the best match
            if selected_app is None:
                selected_app = app_candidates[0][0]

        # Perform the window switch using configured method
        return self._switch_window_with_mode(selected_app)

    def _switch_window_with_mode(self, selected_app: App) -> tuple[str, int]:
        """Switch to window using the configured switching mode."""

        # Restore window if minimized (common for all modes)
        if selected_app.status == "Minimized":
            try:
                import win32con
                import win32gui

                win32gui.ShowWindow(selected_app.handle, win32con.SW_RESTORE)
            except ImportError:
                # Fallback for systems without win32gui
                pass

        if WINDOW_SWITCH_MODE == "foreground":
            return self._switch_foreground_mode(selected_app)
        elif WINDOW_SWITCH_MODE == "topmost_safe":
            return self._switch_topmost_safe_mode(selected_app)
        elif WINDOW_SWITCH_MODE == "topmost_legacy":
            return self._switch_topmost_legacy_mode(selected_app)
        else:
            # Unknown mode, fallback to foreground
            return self._switch_foreground_mode(selected_app)

    def _switch_foreground_mode(self, selected_app: App) -> tuple[str, int]:
        """Switch using win32gui.SetForegroundWindow (non-tainting)."""
        try:
            import win32gui

            win32gui.SetForegroundWindow(selected_app.handle)
            return (
                f"Switched to '{selected_app.title}' (Handle: {selected_app.handle}) [foreground]",
                0,
            )
        except ImportError:
            # win32gui not available, fallback to topmost_safe
            return self._switch_topmost_safe_mode(selected_app)
        except Exception as e:
            return (f"Error switching to '{selected_app.title}': {str(e)}", 1)

    def _switch_topmost_safe_mode(self, selected_app: App) -> tuple[str, int]:
        """Switch using SetWindowTopmost with immediate removal (safe fallback)."""
        try:
            if SetWindowTopmost(selected_app.handle, isTopmost=True):
                # Immediately remove topmost flag to prevent stickiness
                SetWindowTopmost(selected_app.handle, isTopmost=False)
                return (
                    f"Switched to '{selected_app.title}' (Handle: {selected_app.handle}) [topmost_safe]",
                    0,
                )
            else:
                return (f"Failed to switch to '{selected_app.title}'", 1)
        except Exception as e:
            return (f"Error switching to '{selected_app.title}': {str(e)}", 1)

    def _switch_topmost_legacy_mode(self, selected_app: App) -> tuple[str, int]:
        """Switch using original SetWindowTopmost behavior (may cause sticky windows)."""
        try:
            if SetWindowTopmost(selected_app.handle, isTopmost=True):
                return (
                    f"Switched to '{selected_app.title}' (Handle: {selected_app.handle}) [topmost_legacy]",
                    0,
                )
            else:
                return (f"Failed to switch to '{selected_app.title}'", 1)
        except Exception as e:
            return (f"Error switching to '{selected_app.title}': {str(e)}", 1)

    def get_app_size(self, control: Control):
        window = control.BoundingRectangle
        if window.isempty():
            return Size(width=0, height=0)
        return Size(width=window.width(), height=window.height())

    def is_app_visible(self, app) -> bool:
        is_minimized = self.get_app_status(app) != "Minimized"
        size = self.get_app_size(app)
        area = size.width * size.height
        is_overlay = self.is_overlay_app(app)
        return not is_overlay and is_minimized and area > 10

    def is_overlay_app(self, element: Control) -> bool:
        no_children = len(element.GetChildren()) == 0
        is_name = "Overlay" in element.Name.strip()
        return no_children or is_name

    def get_apps(self) -> list[App]:
        try:
            sleep(0.75)
            desktop = GetRootControl()  # Get the desktop control
            elements = desktop.GetChildren()
            apps = []
            for depth, element in enumerate(elements):
                if element.Name in EXCLUDED_APPS or self.is_overlay_app(element):
                    continue
                if element.ControlType in [
                    ControlType.WindowControl,
                    ControlType.PaneControl,
                ]:
                    status = self.get_app_status(element)
                    size = self.get_app_size(element)

                    # Get window title - try multiple properties for best coverage
                    title = ""
                    try:
                        # Try WindowText first (most common for window titles)
                        title = getattr(element, "WindowText", "") or ""
                        # Fallback to Name if WindowText is empty
                        if not title.strip():
                            title = element.Name or ""
                        # Some windows have no meaningful title, use process name as fallback
                        if not title.strip() or title == element.Name:
                            try:
                                from psutil import Process

                                process = Process(element.ProcessId)
                                title = f"{element.Name} - {process.name()}"
                            except:
                                title = element.Name or "Unknown Window"
                    except:
                        title = element.Name or "Unknown Window"

                    apps.append(
                        App(
                            name=element.Name,
                            title=title,
                            depth=depth,
                            status=status,
                            size=size,
                            handle=element.NativeWindowHandle,
                        )
                    )
        except Exception as ex:
            print(f"Error: {ex}")
            apps = []
        return apps

    def screenshot_in_bytes(self, screenshot: Image.Image) -> bytes:
        io = BytesIO()
        screenshot.save(io, format="PNG")
        bytes = io.getvalue()
        return bytes

    def get_screenshot(self, scale: float = 0.7) -> Image.Image:
        screenshot = pyautogui.screenshot()
        size = (screenshot.width * scale, screenshot.height * scale)
        screenshot.thumbnail(size=size, resample=Image.Resampling.LANCZOS)
        return screenshot
