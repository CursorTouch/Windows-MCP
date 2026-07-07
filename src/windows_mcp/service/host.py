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

import logging
import threading
from typing import Any

from .protocol import PIPE_NAME, PIPE_BUFFER_SIZE, Request, Response
from . import policy, secure_desktop

logger = logging.getLogger(__name__)


def _setup_file_logging() -> None:
    """Write service logs to a world-readable file (no stdout in a service).

    The service runs as SYSTEM. Logging under %TEMP% (SYSTEM's profile temp)
    makes the file unreadable by the interactive user, so diagnosing the
    service used to require an elevated dump. Prefer
    ``%ProgramData%\\windows-mcp\\host.log`` — ProgramData is readable by
    Users by default, so an ordinary (non-elevated) process can tail the
    service log. Fall back to %TEMP% if ProgramData can't be created.
    """
    import os
    candidates = []
    program_data = os.environ.get("ProgramData")
    if program_data:
        candidates.append(os.path.join(program_data, "windows-mcp"))
    candidates.append(os.environ.get("TEMP", "C:\\Temp"))

    log_path = None
    for base in candidates:
        try:
            os.makedirs(base, exist_ok=True)
            log_path = os.path.join(base, "windows-mcp-host.log")
            break
        except Exception:
            continue
    if log_path is None:
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

# Pipe-specific access flags. FILE_ALL_ACCESS = 0x1F01FF — full read/write on
# the pipe handle. We grant this to the two principals we trust:
#   - SYSTEM:                    the service itself
#   - Active console user SID:   the broker process running in their session
_FILE_ALL_ACCESS = 0x1F01FF

# Identifiers for the well-known SIDs we need.
# win32security.WinLocalSystemSid → S-1-5-18 (NT AUTHORITY\SYSTEM)
# win32security.WinInteractiveSid  → S-1-5-4  (NT AUTHORITY\INTERACTIVE)
_SID_TYPE_SYSTEM = "WinLocalSystemSid"
_SID_TYPE_INTERACTIVE = "WinInteractiveSid"


def _console_user_sid() -> Any | None:
    """Return the SID of the user logged on to the physical console, or None.

    Pattern:
        WTSGetActiveConsoleSessionId → WTSQueryUserToken → GetTokenInformation
        with TokenUser → SID.

    Returns None on services like CI where no interactive session exists yet.
    """
    if not _WIN32_AVAILABLE:
        return None
    try:
        import win32ts
        import win32api
        session_id = win32ts.WTSGetActiveConsoleSessionId()
        # 0xFFFFFFFF (~0) means no active session.
        if session_id is None or session_id == 0xFFFFFFFF:
            logger.info("No active console session yet")
            return None
        token = win32ts.WTSQueryUserToken(session_id)
        try:
            token_user = win32security.GetTokenInformation(
                token, win32security.TokenUser
            )
            # GetTokenInformation(TokenUser) returns a tuple (SID, attrs).
            sid = token_user[0]
            logger.info(
                "Console user SID for session %d: %s",
                session_id, win32security.ConvertSidToStringSid(sid),
            )
            return sid
        finally:
            win32api.CloseHandle(token)
    except Exception as exc:
        logger.warning("Could not resolve console user SID: %s", exc)
        return None


def _build_pipe_sa() -> Any:
    """Return a SECURITY_ATTRIBUTES that allows only SYSTEM + the console user.

    Falls back to SYSTEM + NT AUTHORITY\\INTERACTIVE (S-1-5-4) when no console
    user is logged in yet — the typical case at boot, because the service is
    SERVICE_AUTO_START and comes up before anyone logs in. The pipe server
    loop rebuilds this SD for every instance and then blocks on
    ConnectNamedPipe, so the *first* boot-time instance keeps whatever DACL it
    was created with until a client actually connects. If that fallback were
    BUILTIN\\Administrators (the previous behaviour), the broker — which runs
    in the interactive session under the user's *filtered* medium-integrity
    token, where Administrators is a deny-only SID — could never open the pipe,
    the ConnectNamedPipe would never complete, and the loop would never
    recreate the instance with the correct console-user DACL. It would hang
    until a manual service restart. INTERACTIVE is present in every
    interactively-logged-on token (filtered or not), so the boot-time pipe is
    reachable by the interactive user as soon as they log in; once a console
    user is resolved, later instances tighten to SYSTEM + that specific SID.
    The privileged operations behind the pipe are policy-gated regardless.
    Never falls back to a NULL DACL — that was the original mistake.

    Raising would prevent the service from starting; instead, on failure we
    return a SECURITY_ATTRIBUTES with a *deny-all* DACL so the pipe is created
    but unreachable, making the failure obvious in logs rather than silent.
    """
    if not _WIN32_AVAILABLE:
        return None

    import pywintypes

    try:
        # SYSTEM SID — always allowed; the service runs as SYSTEM.
        sid_system = win32security.CreateWellKnownSid(
            getattr(win32security, _SID_TYPE_SYSTEM), None
        )

        # Console user SID (if anyone is logged in); else fall back to the
        # INTERACTIVE group so the pipe is still reachable by the interactive
        # user once they log in (see _build_pipe_sa docstring for why Admins
        # would deadlock the boot-time instance).
        sid_user = _console_user_sid()
        if sid_user is None:
            sid_user = win32security.CreateWellKnownSid(
                getattr(win32security, _SID_TYPE_INTERACTIVE), None
            )
            logger.info("Pipe DACL fallback: SYSTEM + NT AUTHORITY\\INTERACTIVE")
        else:
            logger.info(
                "Pipe DACL: SYSTEM + console user %s",
                win32security.ConvertSidToStringSid(sid_user),
            )

        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, _FILE_ALL_ACCESS, sid_system
        )
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, _FILE_ALL_ACCESS, sid_user
        )

        sd = win32security.SECURITY_DESCRIPTOR()
        sd.SetSecurityDescriptorDacl(True, dacl, False)
        sd.SetSecurityDescriptorOwner(sid_system, False)

        sa = pywintypes.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        return sa

    except Exception as exc:
        # Defensive: empty DACL = deny everyone except the SD owner. The pipe
        # will still be created, but clients won't be able to connect — and
        # the exception is logged loudly so the failure mode is discoverable.
        logger.exception("Failed to build restrictive pipe DACL: %s", exc)
        try:
            empty_dacl = win32security.ACL()
            sd = win32security.SECURITY_DESCRIPTOR()
            sd.SetSecurityDescriptorDacl(True, empty_dacl, False)
            sa = pywintypes.SECURITY_ATTRIBUTES()
            sa.SECURITY_DESCRIPTOR = sd
            return sa
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Request dispatcher
# ---------------------------------------------------------------------------

def _enforce_policy(operation: str) -> tuple[bool, str]:
    """Return (allowed, reason) for an auto-click on the UAC consent dialog.

    Every click routed to the host is a consent-dialog click — the broker only
    forwards clicks whose target pixel is owned by ``consent.exe`` — so the
    consent policy is always the deciding factor.

    Read-only ops (tree walks, publisher lookups) are not gated; agents always
    need visibility into UAC. Only the auto-click is gated, because that is the
    action that would bypass the human.

    Earlier revisions additionally short-circuited to "allowed" unless the input
    desktop reported ``Winlogon``. That read is racy while UAC is up with
    ``PromptOnSecureDesktop=0`` (it flips between ``Default`` and ``Winlogon``),
    which made ``block`` / ``allow_with_match`` enforce only intermittently, so
    the desktop check is dropped in favour of unconditional enforcement.
    """
    pol = policy.read_from_registry()
    try:
        publisher = secure_desktop._spawn_in_user_session("publisher", timeout=15.0)
    except Exception as exc:
        logger.warning("policy: user-session publisher lookup failed: %s", exc)
        publisher = None
    allowed, reason = pol.allows_auto_click(publisher)
    logger.info(
        "policy check: op=%s policy=%s publisher=%r → %s (%s)",
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

            case "uia_click_at":
                allowed, reason = _enforce_policy("uia_click_at")
                if not allowed:
                    return Response(id=req.id, error=f"policy denied: {reason}")
                ok = secure_desktop._spawn_in_user_session(
                    "click_at", str(req.params["x"]), str(req.params["y"])
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
        # FlushFileBuffers blocks until the client has read the response.
        # Without it, the DisconnectNamedPipe below will discard the bytes
        # we just wrote — MSDN: "DisconnectNamedPipe forces all the data
        # that has not been read out of the pipe to be discarded." The
        # race shows up under timing variance: small responses fit the
        # pipe buffer instantly so WriteFile returns before the client
        # has drained it, and the client then reads 0 bytes from a
        # disconnected pipe instead of the actual response.
        win32file.FlushFileBuffers(handle)
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
