"""Named pipe client — runs in the user-mode broker process.

Usage
-----
    from windows_mcp.service.pipe import get_client

    client = get_client()
    if client.is_available():
        name = client.desktop_name()  # "Default" | "Winlogon"
        tree = client.uia_tree()
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .protocol import PIPE_NAME, PIPE_BUFFER_SIZE, CALL_TIMEOUT_MS, Request, Response

logger = logging.getLogger(__name__)

try:
    import win32file
    import win32pipe
    import pywintypes
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

# Availability is cached for this many seconds to avoid a ping on every screenshot.
_AVAILABILITY_CACHE_TTL = 30.0


class HostServiceClient:
    """Client for the Windows MCP host service named pipe."""

    def __init__(self) -> None:
        self._available: bool | None = None
        self._available_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the host service is running and reachable."""
        if not _WIN32_AVAILABLE:
            return False
        now = time.monotonic()
        if self._available is not None and (now - self._available_ts) < _AVAILABILITY_CACHE_TTL:
            return self._available
        try:
            result = self._call("ping", {})
            available = result == "pong"
        except Exception:
            available = False
        self._available = available
        self._available_ts = now
        return available

    def invalidate_cache(self) -> None:
        """Force the next is_available() call to re-check the pipe."""
        self._available = None

    def desktop_name(self) -> str:
        """Return the name of the current input desktop ('Default' or 'Winlogon')."""
        return self._call("desktop_name", {})

    def uia_windows(self) -> list[str]:
        """Return top-level window titles visible on the input desktop."""
        return self._call("uia_windows", {})

    def uia_tree(self) -> list[dict]:
        """Return the full UIA tree of the input desktop as a list of window dicts."""
        return self._call("uia_tree", {})

    def uia_invoke(self, name: str) -> bool:
        """Find and invoke a named element (e.g. 'Yes', 'No') on the input desktop."""
        return self._call("uia_invoke", {"name": name})

    def uia_click_at(self, x: int, y: int) -> bool:
        """Invoke the element at screen coordinates (x, y) on the input desktop."""
        return self._call("uia_click_at", {"x": x, "y": y})

    def uia_type_at(self, x: int, y: int, text: str) -> bool:
        """Set the value of the editable element at (x, y) on the input desktop."""
        return self._call("uia_type_at", {"x": x, "y": y, "text": text})

    def uia_drag_from_to(self, x1: int, y1: int, x2: int, y2: int) -> bool:
        """Move the element at (x1, y1) onto (x2, y2) on the input desktop."""
        return self._call("uia_drag_from_to", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    def get_uac_publisher(self) -> str | None:
        """Return the publisher string from the active UAC dialog, or None."""
        return self._call("get_uac_publisher", {})

    def wait_for_uac_prompt(self, timeout_ms: int = 60_000) -> dict | None:
        """Block until UAC fires on the input desktop, or until *timeout_ms* elapses.

        Returns a dict ``{"desktop": "Winlogon", "publisher": str|None, "tree": [...]}``
        on success, or ``None`` on timeout.
        """
        return self._call("wait_for_uac_prompt", {"timeout_ms": timeout_ms})

    def policy_state(self) -> dict:
        """Return the persisted Secure-Desktop policy and allowlist."""
        return self._call("policy_state", {})

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        if not _WIN32_AVAILABLE:
            raise RuntimeError("pywin32 is not available")

        req = Request(method=method, params=params)
        handle = self._open_with_retry()

        try:
            # Switch to message read mode so we get whole messages back.
            win32pipe.SetNamedPipeHandleState(
                handle,
                win32pipe.PIPE_READMODE_MESSAGE,
                None,
                None,
            )
            win32file.WriteFile(handle, req.encode())
            _, data = win32file.ReadFile(handle, PIPE_BUFFER_SIZE)
        except pywintypes.error as exc:
            raise RuntimeError(f"Pipe I/O error: {exc}") from exc
        finally:
            try:
                win32file.CloseHandle(handle)
            except Exception:
                pass

        resp = Response.decode(data)
        if resp.error:
            raise RuntimeError(f"Host service error ({method}): {resp.error}")
        return resp.result

    def _open_with_retry(self, *, attempts: int = 30, gap_ms: int = 100) -> Any:
        """Open the named pipe, retrying on the brief race window where the
        server has connected one instance but not yet recreated the next.

        WaitNamedPipe returns ERROR_FILE_NOT_FOUND (not ERROR_SEM_TIMEOUT)
        when no instance of the pipe is currently in WAITING_FOR_CONNECT
        state. The host service spends ~20-50 ms between accepting one
        connection and creating the next instance; back-to-back broker
        calls (e.g. is_available() ping immediately followed by an actual
        operation) often land inside that window. Retry with a short gap.
        """
        last_exc: Any = None
        for _ in range(attempts):
            try:
                win32pipe.WaitNamedPipe(PIPE_NAME, CALL_TIMEOUT_MS)
                return win32file.CreateFile(
                    PIPE_NAME,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
            except pywintypes.error as exc:
                last_exc = exc
                if exc.winerror in (2, 231):  # FILE_NOT_FOUND, PIPE_BUSY
                    time.sleep(gap_ms / 1000.0)
                    continue
                raise RuntimeError(f"Cannot connect to host service pipe: {exc}") from exc
        raise RuntimeError(
            f"Cannot connect to host service pipe after {attempts} retries: {last_exc}"
        )


_client: HostServiceClient | None = None


def get_client() -> HostServiceClient:
    """Return the process-wide singleton pipe client."""
    global _client
    if _client is None:
        _client = HostServiceClient()
    return _client
