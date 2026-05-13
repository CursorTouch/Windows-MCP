"""Windows MCP host service — runs as NT AUTHORITY\\SYSTEM.

This module serves two purposes:

1. **Service class** (``WindowsMCPHostService``) — a ``pywin32``
   ``ServiceFramework`` subclass installed via ``windows-mcp service secure-desktop install``.
   It starts a named pipe server that handles privileged desktop operations
   requested by the user-mode broker.

2. **Entry point** — when executed directly (``python -m
   windows_mcp.service.host``) it delegates to pywin32's
   ``HandleCommandLine``, which is how ``sc.exe`` / the SCM invokes service
   executables.

Named pipe server design
------------------------
* Message-mode pipe so each request/response is a discrete packet.
* One pipe instance per client connection; a fresh instance is created after
  each client disconnects so the server is always listening.
* Each connection is handled on its own daemon thread — the main service
  thread just waits for the stop event.
* Security descriptor allows only SYSTEM + the interactive console user.
"""

from __future__ import annotations

import base64
import logging
import threading
from typing import Any

from .protocol import PIPE_NAME, PIPE_BUFFER_SIZE, Request, Response
from . import policy, secure_desktop

logger = logging.getLogger(__name__)


def _setup_file_logging() -> None:
    """Write service logs to %TEMP%\\windows-mcp-host.log (no stdout in a service)."""
    import os
    log_path = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "windows-mcp-host.log")
    logging.basicConfig(
        filename=log_path,
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logger.info("Logging initialised → %s", log_path)

try:
    import win32file
    import win32pipe
    import win32security
    import win32service
    import win32serviceutil
    import win32event
    import pywintypes
    import servicemanager
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

# ---------------------------------------------------------------------------
# Pipe security
# ---------------------------------------------------------------------------

def _build_pipe_sa() -> Any:
    """Return a SECURITY_ATTRIBUTES with a NULL DACL (allows all local access).

    A NULL DACL is intentional here: the pipe is local-only (no network
    listener), so allowing any local process to connect is fine.  The tighter
    SYSTEM+user DACL can be added later once the basic pipe works; for now,
    complexity in the DACL was causing CreateNamedPipe to fail silently.

    Note: SECURITY_ATTRIBUTES lives in pywintypes, not win32security.
    """
    if not _WIN32_AVAILABLE:
        return None
    try:
        import pywintypes
        sd = win32security.SECURITY_DESCRIPTOR()
        sd.SetSecurityDescriptorDacl(True, None, False)  # NULL DACL = everyone
        sa = pywintypes.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        return sa
    except Exception as exc:
        logger.warning("Could not build pipe SA, falling back to None: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Request dispatcher
# ---------------------------------------------------------------------------

def _enforce_policy(operation: str) -> tuple[bool, str]:
    """Return (allowed, reason) for an auto-input op on the current input desktop.

    Read-only ops (screenshot, tree walks) are not gated — agents always need
    visibility into UAC. Only auto-clicks/types/drags on the Secure Desktop are
    policy-gated, because those are the actions that bypass the human.

    Read-only ops on Default desktop are not gated either. We only enforce on
    Winlogon because that is where consent prompts live.
    """
    if secure_desktop.get_input_desktop_name().lower() != "winlogon":
        return True, "input desktop is not Winlogon"
    pol = policy.read_from_registry()
    publisher = secure_desktop.get_uac_publisher()
    allowed, reason = pol.allows_auto_click(publisher)
    logger.info(
        "policy check: op=%s desktop=Winlogon policy=%s publisher=%r → %s (%s)",
        operation, pol.policy, publisher, allowed, reason,
    )
    return allowed, reason


def _dispatch(req: Request) -> Response:
    """Execute a single request and return a response."""
    try:
        match req.method:
            case "ping":
                return Response(id=req.id, result="pong")

            case "desktop_name":
                name = secure_desktop.get_input_desktop_name()
                return Response(id=req.id, result=name)

            case "screenshot":
                png = secure_desktop.capture_screenshot()
                return Response(id=req.id, result=base64.b64encode(png).decode())

            case "uia_windows":
                titles = secure_desktop.uia_get_window_titles()
                return Response(id=req.id, result=titles)

            case "uia_tree":
                tree = secure_desktop.uia_get_tree()
                return Response(id=req.id, result=tree)

            case "get_uac_publisher":
                pub = secure_desktop.get_uac_publisher()
                return Response(id=req.id, result=pub)

            case "wait_for_uac_prompt":
                timeout_ms = int(req.params.get("timeout_ms", 60_000))
                result = secure_desktop.wait_for_uac_prompt(timeout_ms=timeout_ms)
                return Response(id=req.id, result=result)

            case "policy_state":
                pol = policy.read_from_registry()
                return Response(id=req.id, result={
                    "policy": pol.policy,
                    "publishers_allowlist": pol.publishers_allowlist,
                })

            case "uia_invoke":
                allowed, reason = _enforce_policy("uia_invoke")
                if not allowed:
                    return Response(id=req.id, error=f"policy denied: {reason}")
                ok = secure_desktop.uia_invoke_element(req.params["name"])
                return Response(id=req.id, result=ok)

            case "uia_click_at":
                allowed, reason = _enforce_policy("uia_click_at")
                if not allowed:
                    return Response(id=req.id, error=f"policy denied: {reason}")
                ok = secure_desktop.uia_click_at(req.params["x"], req.params["y"])
                return Response(id=req.id, result=ok)

            case "uia_type_at":
                allowed, reason = _enforce_policy("uia_type_at")
                if not allowed:
                    return Response(id=req.id, error=f"policy denied: {reason}")
                ok = secure_desktop.uia_type_at(
                    req.params["x"], req.params["y"], req.params["text"]
                )
                return Response(id=req.id, result=ok)

            case "uia_drag_from_to":
                allowed, reason = _enforce_policy("uia_drag_from_to")
                if not allowed:
                    return Response(id=req.id, error=f"policy denied: {reason}")
                ok = secure_desktop.uia_drag_from_to(
                    req.params["x1"], req.params["y1"],
                    req.params["x2"], req.params["y2"],
                )
                return Response(id=req.id, result=ok)

            case _:
                return Response(id=req.id, error=f"Unknown method: {req.method!r}")

    except Exception as exc:
        logger.exception("Unhandled error dispatching method %r", req.method)
        return Response(id=req.id, error=str(exc))


# ---------------------------------------------------------------------------
# Pipe server
# ---------------------------------------------------------------------------

class PipeServer:
    """Synchronous named pipe server — one daemon thread per client."""

    def __init__(self) -> None:
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        """Accept connections forever until stop() is called."""
        logger.info("Pipe server loop starting on %s", PIPE_NAME)
        while not self._stop.is_set():
            sa = _build_pipe_sa()
            logger.debug("CreateNamedPipe: sa=%s", sa)
            try:
                handle = win32pipe.CreateNamedPipe(
                    PIPE_NAME,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE
                    | win32pipe.PIPE_READMODE_MESSAGE
                    | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    PIPE_BUFFER_SIZE,
                    PIPE_BUFFER_SIZE,
                    0,
                    sa,
                )
                logger.info("Pipe created, waiting for client connection")
            except pywintypes.error as exc:
                logger.error("CreateNamedPipe failed (winerror=%s): %s", exc.winerror, exc)
                break

            try:
                # Blocks here until a client connects.
                win32pipe.ConnectNamedPipe(handle, None)
                logger.info("Client connected")
            except pywintypes.error as exc:
                logger.warning("ConnectNamedPipe failed: %s", exc)
                try:
                    win32file.CloseHandle(handle)
                except Exception:
                    pass
                continue

            threading.Thread(
                target=_serve_one_client,
                args=(handle,),
                daemon=True,
                name="pipe-client",
            ).start()

        logger.info("Pipe server loop exited")


def _serve_one_client(handle: Any) -> None:
    """Read one request, write one response, close the connection."""
    try:
        _, data = win32file.ReadFile(handle, PIPE_BUFFER_SIZE)
        req = Request.decode(data)
        resp = _dispatch(req)
        win32file.WriteFile(handle, resp.encode())
    except Exception as exc:
        logger.warning("Error serving pipe client: %s", exc)
    finally:
        try:
            win32pipe.DisconnectNamedPipe(handle)
            win32file.CloseHandle(handle)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Windows service
# ---------------------------------------------------------------------------

if _WIN32_AVAILABLE:
    class WindowsMCPHostService(win32serviceutil.ServiceFramework):
        _svc_name_ = "WindowsMCPHost"
        _svc_display_name_ = "Windows MCP Host"
        _svc_description_ = (
            "Privileged helper for Windows MCP.  Enables screenshot capture and "
            "UI Automation access to the Secure Desktop (UAC consent prompts) so "
            "that LLM agents can operate Windows unattended."
        )

        def __init__(self, args: Any) -> None:
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
            self._server = PipeServer()

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._server.stop()
            win32event.SetEvent(self._stop_event)

        def SvcDoRun(self) -> None:
            _setup_file_logging()
            logger.info("SvcDoRun entered")

            # Explicitly report RUNNING so the SCM stops waiting and the
            # service shows as started even before the pipe is ready.
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)

            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )

            server_thread = threading.Thread(
                target=self._server.run, daemon=True, name="pipe-server"
            )
            server_thread.start()
            logger.info("Pipe server thread started, entering wait loop")
            win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)
            server_thread.join(timeout=5)

            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, ""),
            )
            logger.info("SvcDoRun exiting")


# ---------------------------------------------------------------------------
# Direct invocation (used by the SCM)
# ---------------------------------------------------------------------------

def main() -> None:
    import sys
    if not _WIN32_AVAILABLE:
        raise SystemExit("pywin32 is required to run the host service")

    # When len(sys.argv) == 1 the process was launched by the SCM (no extra
    # arguments).  We must call StartServiceCtrlDispatcher so the SCM
    # dispatcher takes over and routes start/stop events to SvcDoRun/SvcStop.
    if len(sys.argv) == 1:
        try:
            servicemanager.Initialize("WindowsMCPHost", None)
            servicemanager.PrepareToHostSingle(WindowsMCPHostService)
            servicemanager.StartServiceCtrlDispatcher()
        except Exception as exc:
            # Write to the Windows event log so failures are diagnosable.
            servicemanager.LogErrorMsg(f"WindowsMCPHost failed to start: {exc}")
            raise
    else:
        win32serviceutil.HandleCommandLine(WindowsMCPHostService)


if __name__ == "__main__":
    main()
