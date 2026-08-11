import logging
import signal
import subprocess
from xml.sax.saxutils import escape as xml_escape

import psutil

__all__ = [
    "run_with_graceful_timeout",
    "ps_quote",
    "ps_quote_for_xml",
]

logger = logging.getLogger(__name__)


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
        grace_period: Seconds to wait after CTRL_BREAK before force-killing. Defaults to 2.0.

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

        1. First, try a graceful stop by sending ``CTRL_BREAK_EVENT`` to the
           child process group, so console applications have a chance to exit
           cleanly.
        2. If that still does not finish within ``grace_period``, forcefully
           terminate the whole process tree via ``taskkill /T /F``.

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

    # Windows graceful-stop prerequisite: CREATE_NEW_PROCESS_GROUP is required
    # so that send_signal(CTRL_BREAK_EVENT) targets the child process group
    # rather than the current process (which would cause it to exit).
    creationflags = kwargs.get("creationflags", 0)
    creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
    kwargs["creationflags"] = creationflags

    with subprocess.Popen(*popenargs, **kwargs) as process:
        stdout = stderr = None
        try:
            stdout, stderr = process.communicate(input=input, timeout=timeout)

        except subprocess.TimeoutExpired as exc1:
            # Try graceful shutdown first
            logger.debug('Process did not exit within timeout, attempting graceful shutdown.')
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception:
                logger.debug('Failed to send CTRL_BREAK_EVENT, attempting to terminate process.')

            try:
                exc1.stdout, exc1.stderr = process.communicate(timeout=grace_period)
                logger.debug("Process exited after CTRL_BREAK, re-raising original TimeoutExpired.")
                exc1.add_note("Process exited after graceful CTRL_BREAK shutdown.")
                raise exc1  # (1)
            except subprocess.TimeoutExpired as exc2:
                if exc2 is exc1:  # Raised from the previous attempt (1)
                    # No need to try further shutdown
                    raise exc2

                # Kill the whole tree as a last resort
                logger.debug(
                    f"Process {process.pid} (exist: {check_pid_exists(process.pid)}) did not exit gracefully after {grace_period} seconds, killing it and all child processes..."
                )
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

                try:
                    exc2.stdout, exc2.stderr = process.communicate(timeout=grace_period)
                except subprocess.TimeoutExpired:
                    # Do not replace the original timeout exception
                    pass

                exc2.add_note(
                    f"Process killed after failing to exit gracefully within {grace_period} seconds."
                )
                raise exc2

        except BaseException:
            # Keep cleanup strategy consistent with timeout path
            logger.debug('Other exception occurred, attempting to kill process...')
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            raise

        retcode = process.poll()
        if check and retcode:
            raise subprocess.CalledProcessError(
                retcode, process.args, output=stdout, stderr=stderr
            )

        return subprocess.CompletedProcess(process.args, retcode, stdout, stderr)
