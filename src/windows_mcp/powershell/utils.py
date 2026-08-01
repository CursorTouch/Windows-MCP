import logging
import subprocess
from xml.sax.saxutils import escape as xml_escape

import psutil

__all__ = [
    "run_with_graceful_timeout",
    "ps_quote",
    "ps_quote_for_xml",
]

logger = logging.getLogger(__name__)

_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _kill_process_tree(process: subprocess.Popen, wait_timeout: float) -> None:
    """Terminate only the spawned subprocess tree, never the MCP host process group."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=max(1.0, wait_timeout),
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass



def ps_quote(value: str) -> str:
    """Wrap value in PowerShell single-quoted string literal (escapes ' as '')."""
    return "'" + value.replace("'", "''") + "'"


def ps_quote_for_xml(value: str) -> str:
    """XML-escape then ps_quote. Use for values in XML passed to PowerShell."""
    escaped = xml_escape(value, {'"': '&quot;', "'": '&apos;'})
    return ps_quote(escaped)


def check_pid_exists(pid: int) -> bool:
    """Check whether a process with the given PID is actively running."""
    try:
        proc = psutil.Process(pid)
        return proc.status() not in (psutil.STATUS_DEAD, psutil.STATUS_ZOMBIE)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def run_with_graceful_timeout(
        *popenargs,
        input=None,
        capture_output=False,
        timeout=None,
        check=False,
        grace_period: float = 2.0,
        **kwargs,
):
    """A Windows-oriented variant migrated from ``subprocess.run``.

    This helper keeps the overall calling style and behavior of
    ``subprocess.run``, but adapts the timeout-handling path for some
    Windows-specific edge cases as described below.

    Args:
        *popenargs: Positional arguments to pass to ``subprocess.Popen``.
        input: Data to send to stdin (if not None).
        capture_output: If True, capture stdout and stderr into the returned CompletedProcess.
        timeout: Seconds to wait for process to complete before triggering shutdown.
        check: If True, raise CalledProcessError if the process exits with a non-zero code.
        grace_period: Seconds reserved for subprocess-tree cleanup after timeout. Defaults to 2.0.

    Notes:
        In some Windows scenarios, especially when launching a console host
        such as PowerShell and letting it start another interactive console
        process or a process stuck in an infinite loop that continuously outputs data
        (for example ``pwsh -> python``, like ``pwsh -NoProfile -Command python``
        or ``pwsh -NoProfile -Command "python -c 'while True: print(1)'"``),
        the standard timeout flow of ``subprocess.run`` may not be sufficient.
        After a timeout occurs, simply terminating the top-level child process
        may still leave descendant processes alive, or leave inherited pipe handles open.
        As a result, the parent process can remain blocked while trying to
        finish the final ``communicate()`` cleanup, and memory usage may continue to grow if
        stdout/stderr are being captured.

        To make this case more robust, this function changes the timeout path
        into a two-stage shutdown strategy:

        On timeout or cancellation, terminate only the spawned subprocess tree
        via ``taskkill /T /F``. Console control signals are intentionally not
        used because they can propagate beyond the child tree and terminate the
        long-running MCP host on Windows.

        Related issues: #124, #146
    """

    if input is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used.")
        kwargs["stdin"] = subprocess.PIPE

    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output.")
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE

    # Isolate the child from the MCP host console and process group. This keeps
    # timeout/cancellation cleanup from propagating CTRL_C/CTRL_BREAK to the server.
    creationflags = kwargs.get("creationflags", 0)
    creationflags |= _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
    kwargs["creationflags"] = creationflags

    with subprocess.Popen(*popenargs, **kwargs) as process:
        stdout = stderr = None
        try:
            stdout, stderr = process.communicate(input=input, timeout=timeout)

        except subprocess.TimeoutExpired as exc:
            logger.debug(
                "Process exceeded timeout; terminating isolated subprocess tree."
            )
            _kill_process_tree(process, grace_period)
            try:
                exc.stdout, exc.stderr = process.communicate(timeout=grace_period)
            except subprocess.TimeoutExpired:
                pass
            exc.add_note(
                "Isolated subprocess tree terminated after command timeout."
            )
            raise
        except BaseException:
            logger.debug(
                "Command execution was cancelled; terminating isolated subprocess tree."
            )
            _kill_process_tree(process, grace_period)
            raise

        retcode = process.poll()
        if check and retcode:
            raise subprocess.CalledProcessError(
                retcode, process.args, output=stdout, stderr=stderr
            )

        return subprocess.CompletedProcess(process.args, retcode, stdout, stderr)
