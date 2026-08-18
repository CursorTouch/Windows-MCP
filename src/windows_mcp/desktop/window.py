"""Exact top-level window discovery, activation, and bounds control."""

import ctypes
from ctypes import wintypes
import logging
import os
from time import perf_counter, sleep
from typing import Any, Literal

from psutil import Process
import win32con
import win32gui
import win32process

import windows_mcp.uia as uia
from windows_mcp.desktop.views import Status


logger = logging.getLogger(__name__)


class ExactWindowController:
    """Operate on exact native windows and reuse Desktop foreground switching."""

    def __init__(self, desktop: Any) -> None:
        self._desktop = desktop

    @staticmethod
    def _window_process_name(process_id: int) -> str | None:
        try:
            return Process(process_id).name()
        except Exception:
            return None

    @staticmethod
    def _window_process_path(process_id: int) -> str | None:
        try:
            return Process(process_id).exe()
        except Exception:
            return None

    @staticmethod
    def _client_bounds(handle: int) -> dict[str, int]:
        left, top, right, bottom = win32gui.GetClientRect(handle)
        screen_left, screen_top = win32gui.ClientToScreen(handle, (left, top))
        width = right - left
        height = bottom - top
        return {
            "left": screen_left,
            "top": screen_top,
            "width": width,
            "height": height,
            "right": screen_left + width,
            "bottom": screen_top + height,
        }

    @staticmethod
    def _outer_bounds(handle: int) -> dict[str, int]:
        left, top, right, bottom = win32gui.GetWindowRect(handle)
        return {
            "left": left,
            "top": top,
            "width": right - left,
            "height": bottom - top,
            "right": right,
            "bottom": bottom,
        }

    def _window_identity_from_handle(
        self,
        handle: int,
        *,
        title: str | None = None,
        status: str | None = None,
        process_id: int | None = None,
    ) -> dict[str, object]:
        if process_id is None:
            _, process_id = win32process.GetWindowThreadProcessId(handle)
        if title is None:
            title = win32gui.GetWindowText(handle)
        if status is None:
            if uia.IsIconic(handle):
                status = Status.MINIMIZED.value
            elif uia.IsZoomed(handle):
                status = Status.MAXIMIZED.value
            elif uia.IsWindowVisible(handle):
                status = Status.NORMAL.value
            else:
                status = Status.HIDDEN.value
        return {
            "handle": handle,
            "process_id": process_id,
            "process": self._window_process_name(process_id),
            "process_path": self._window_process_path(process_id),
            "title": title,
            "status": status,
            "outer": self._outer_bounds(handle),
            "client": self._client_bounds(handle),
        }

    @staticmethod
    def _validate_exact_identity_values(
        *,
        title: str | None,
        process: str | None,
        process_id: int | None,
        handle: int | None,
    ) -> None:
        if title == "":
            raise ValueError("title must not be empty")
        if process == "":
            raise ValueError("process must not be empty")
        for name, value in (("process_id", process_id), ("handle", handle)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")

    @staticmethod
    def _validate_window_bounds(bounds: list[int] | None, name: str) -> list[int] | None:
        if bounds is None:
            return None
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise ValueError(f"{name} must contain exactly 4 integers")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in bounds):
            raise ValueError(f"{name} must contain exactly 4 integers")
        parsed = list(bounds)
        if parsed[2] <= 0 or parsed[3] <= 0:
            raise ValueError(f"{name} width and height must be greater than zero")
        return parsed

    @staticmethod
    def _bounds_match_with_tolerance(
        actual: dict[str, int],
        expected: list[int],
        *,
        tolerance: int = 1,
    ) -> bool:
        keys = ("left", "top", "width", "height")
        return all(
            abs(actual[key] - expected[index]) <= tolerance for index, key in enumerate(keys)
        )

    def find_exact_windows(
        self,
        title: str | None = None,
        title_match: Literal["exact", "contains"] = "contains",
        process: str | None = None,
        process_id: int | None = None,
        handle: int | None = None,
    ) -> list[dict[str, object]]:
        """Find top-level windows using explicit identity filters."""
        if title_match not in {"exact", "contains"}:
            raise ValueError('title_match must be "exact" or "contains"')
        self._validate_exact_identity_values(
            title=title,
            process=process,
            process_id=process_id,
            handle=handle,
        )

        handles: list[int] = [handle] if handle is not None else []
        if handle is None:
            win32gui.EnumWindows(lambda candidate, _: handles.append(candidate) or True, None)
        matches = []
        expected_process = os.path.basename(process).casefold() if process else None
        for candidate in handles:
            if handle is not None and candidate != handle:
                continue
            if not win32gui.IsWindow(candidate):
                continue
            _, candidate_process_id = win32process.GetWindowThreadProcessId(candidate)
            if process_id is not None and candidate_process_id != process_id:
                continue
            candidate_title = win32gui.GetWindowText(candidate)
            if title is not None:
                actual_title = candidate_title.casefold()
                expected_title = title.casefold()
                if title_match == "exact" and actual_title != expected_title:
                    continue
                if title_match == "contains" and expected_title not in actual_title:
                    continue
            if expected_process is not None:
                process_name = self._window_process_name(candidate_process_id)
                if process_name is None or process_name.casefold() != expected_process:
                    continue
            matches.append(
                self._window_identity_from_handle(
                    candidate,
                    title=candidate_title,
                    process_id=candidate_process_id,
                )
            )
        return matches

    def _require_exact_window(
        self,
        handle: int,
        process_id: int | None = None,
        process: str | None = None,
        title: str | None = None,
        title_match: Literal["exact", "contains"] = "contains",
    ) -> dict[str, object]:
        self._validate_exact_identity_values(
            title=title,
            process=process,
            process_id=process_id,
            handle=handle,
        )
        if not win32gui.IsWindow(handle):
            raise ValueError("Invalid or stale window handle")
        matches = self.find_exact_windows(
            title=title,
            title_match=title_match,
            process=process,
            process_id=process_id,
            handle=handle,
        )
        if not matches:
            raise ValueError("No window matched the supplied identity")
        if len(matches) > 1:
            raise ValueError("Window identity was ambiguous")
        return matches[0]

    def activate_exact_window(
        self,
        handle: int,
        process_id: int | None = None,
        process: str | None = None,
        title: str | None = None,
        title_match: Literal["exact", "contains"] = "contains",
    ) -> dict[str, object]:
        """Activate an exact window and verify foreground readback."""
        self._require_exact_window(handle, process_id, process, title, title_match)
        if win32gui.GetForegroundWindow() != handle:
            self._desktop.bring_window_to_top(handle)
        foreground_handle = win32gui.GetForegroundWindow()
        if foreground_handle != handle:
            raise ValueError(
                f"Failed to activate exact window: foreground handle is {foreground_handle}"
            )
        return self._require_exact_window(handle, process_id, process, title, title_match)

    def set_exact_window_bounds(
        self,
        handle: int,
        outer: list[int] | None = None,
        client: list[int] | None = None,
        process_id: int | None = None,
        process: str | None = None,
        title: str | None = None,
        title_match: Literal["exact", "contains"] = "contains",
    ) -> dict[str, object]:
        """Set outer or client bounds and return the actual applied geometry."""
        outer = self._validate_window_bounds(outer, "outer")
        client = self._validate_window_bounds(client, "client")
        if (outer is None) == (client is None):
            raise ValueError("Provide exactly one of outer or client bounds")
        self._require_exact_window(handle, process_id, process, title, title_match)

        if outer is not None:
            x, y, width, height = outer
        else:
            x, y, width, height = self._outer_bounds_for_client(handle, client)

        win32gui.MoveWindow(handle, x, y, width, height, True)
        return self._wait_for_exact_window_bounds(
            handle=handle,
            process_id=process_id,
            process=process,
            title=title,
            title_match=title_match,
            outer=outer,
            client=client,
        )

    def _outer_bounds_for_client(
        self,
        handle: int,
        client: list[int],
    ) -> tuple[int, int, int, int]:
        client_x, client_y, client_width, client_height = client
        try:
            style = win32gui.GetWindowLong(handle, win32con.GWL_STYLE)
            ex_style = win32gui.GetWindowLong(handle, win32con.GWL_EXSTYLE)
            rect = wintypes.RECT(0, 0, client_width, client_height)
            has_menu = bool(win32gui.GetMenu(handle))
            dpi = ctypes.windll.user32.GetDpiForWindow(handle)
            adjust_for_dpi = getattr(ctypes.windll.user32, "AdjustWindowRectExForDpi", None)
            if adjust_for_dpi is not None:
                ok = adjust_for_dpi(
                    ctypes.byref(rect),
                    style,
                    has_menu,
                    ex_style,
                    dpi,
                )
            else:
                ok = ctypes.windll.user32.AdjustWindowRectEx(
                    ctypes.byref(rect),
                    style,
                    has_menu,
                    ex_style,
                )
            if not ok:
                raise OSError("AdjustWindowRectEx failed")
            border_left = -rect.left
            border_top = -rect.top
            outer_width = rect.right - rect.left
            outer_height = rect.bottom - rect.top
            return client_x - border_left, client_y - border_top, outer_width, outer_height
        except Exception as exc:
            logger.debug("Falling back to current window frame deltas: %s", exc)
            current_outer = self._outer_bounds(handle)
            current_client = self._client_bounds(handle)
            return (
                client_x - (current_client["left"] - current_outer["left"]),
                client_y - (current_client["top"] - current_outer["top"]),
                client_width + (current_outer["width"] - current_client["width"]),
                client_height + (current_outer["height"] - current_client["height"]),
            )

    def _wait_for_exact_window_bounds(
        self,
        *,
        handle: int,
        process_id: int | None,
        process: str | None,
        title: str | None,
        title_match: Literal["exact", "contains"],
        outer: list[int] | None,
        client: list[int] | None,
        timeout: float = 2.0,
    ) -> dict[str, object]:
        deadline = perf_counter() + timeout
        last_identity = self._require_exact_window(handle, process_id, process, title, title_match)
        while True:
            target = outer or client
            bounds_type = "outer" if outer is not None else "client"
            if self._bounds_match_with_tolerance(last_identity[bounds_type], target):
                return last_identity
            if perf_counter() >= deadline:
                raise TimeoutError(
                    f"Window {bounds_type} bounds did not reach requested target: "
                    f"expected {target}, actual {last_identity[bounds_type]}"
                )
            if client is not None:
                actual_client = last_identity["client"]
                actual_outer = last_identity["outer"]
                win32gui.MoveWindow(
                    handle,
                    actual_outer["left"] + client[0] - actual_client["left"],
                    actual_outer["top"] + client[1] - actual_client["top"],
                    actual_outer["width"] + client[2] - actual_client["width"],
                    actual_outer["height"] + client[3] - actual_client["height"],
                    True,
                )
            sleep(0.05)
            last_identity = self._require_exact_window(
                handle,
                process_id,
                process,
                title,
                title_match,
            )
