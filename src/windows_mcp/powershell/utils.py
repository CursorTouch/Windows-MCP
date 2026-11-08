import errno
import io
import logging
import re
import signal
import subprocess
import threading
import time
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape

import psutil

__all__ = [
    "run_with_graceful_timeout",
    "ps_quote",
    "ps_quote_for_xml",
    "decode_clixml",
]

logger = logging.getLogger(__name__)


# PowerShell ends a single-quoted string at the typographic single quotes U+2018 to
# U+201B as well as at the ASCII apostrophe, and reads any of them doubled as one
# literal quote, so all five are doubled.
_DOUBLE_SINGLE_QUOTES = str.maketrans({q: q * 2 for q in "'\u2018\u2019\u201a\u201b"})


def ps_quote(value: str) -> str:
    """Wrap value in PowerShell single-quoted string literal (escapes ' as '', and
    the typographic single quotes the same way)."""
    return "'" + value.translate(_DOUBLE_SINGLE_QUOTES) + "'"


def ps_quote_for_xml(value: str) -> str:
    """XML-escape then ps_quote. Use for values in XML passed to PowerShell."""
    escaped = xml_escape(value, {'"': '&quot;', "'": '&apos;'})
    return ps_quote(escaped)


# Windows PowerShell 5.1 started with -EncodedCommand writes its error, warning,
# verbose and debug streams to stderr as CLIXML (a serialized object stream)
# rather than text, whatever -OutputFormat says. Native programs the script runs
# write to the same handle in plain text, so their output can sit between the
# "#< CLIXML" marker and the <Objs> document.
_CLIXML_MARKER = "#< CLIXML"
_CLIXML_DOCUMENT = re.compile(r"<Objs\b.*?</Objs>", re.DOTALL)
_CLIXML_ESCAPE = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _clixml_unescape(text: str) -> str:
    # PowerShell writes characters XML cannot hold (CR, LF, other controls) as
    # _xHHHH_, and a literal "_x" as _x005F_x.
    return _CLIXML_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)


def decode_clixml(text: str) -> str:
    """Return the text of PowerShell stream records in *text* if it is CLIXML.

    Keeps the string records (error, warning, verbose, debug) in order, drops
    progress records such as "Preparing modules for first use.", and keeps any
    plain text around the documents. Text that is not CLIXML, or a document
    that does not parse, is returned unchanged.
    """
    if _CLIXML_MARKER not in text:
        return text

    def records(match: re.Match) -> str:
        try:
            root = ElementTree.fromstring(match.group(0))
        except ElementTree.ParseError:
            return match.group(0)
        return "".join(
            _clixml_unescape(node.text or "")
            for node in root
            if node.tag.rpartition("}")[2] == "S"
        )

    text = text.replace(_CLIXML_MARKER + "\r\n", "", 1).replace(_CLIXML_MARKER, "", 1)
    return _CLIXML_DOCUMENT.sub(records, text)


def check_pid_exists(pid: int) -> bool:
    """Check whether a process with the given PID is actively running."""
    try:
        proc = psutil.Process(pid)
        return proc.status() not in (psutil.STATUS_DEAD, psutil.STATUS_ZOMBIE)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


#: How long to keep reading the output pipes after the process has exited. A
#: program the command started may hold inherited copies of the pipes (#578);
#: EOF then waits for that program, but what the process itself wrote is
#: already in the pipe and is read at once.
PIPE_DRAIN_SECONDS = 0.5


class _PipeReader:
    """Read one output pipe on a thread, keeping what has arrived so far.

    ``Popen.communicate()`` returns only at EOF on every pipe. A program the
    command started without waiting for it, and that inherited the pipe
    handles (``Start-Process`` with ``-RedirectStandardOutput`` does this),
    keeps the pipe open until it exits, so the call waited for that program.
    """

    def __init__(self, stream):
        self._stream = stream
        self._chunks: list[bytes] = []
        # In text mode, read the bytes and decode at the end, as communicate() does.
        self._encoding = getattr(stream, "encoding", None)
        self._errors = getattr(stream, "errors", None)
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self):
        raw = getattr(self._stream, "buffer", self._stream)
        try:
            while chunk := raw.read1(65536):
                self._chunks.append(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._stream.close()
            except (OSError, ValueError):
                pass

    def join(self, deadline: float) -> None:
        self._thread.join(max(0.0, deadline - time.monotonic()))

    def output(self):
        data = b"".join(list(self._chunks))
        if self._encoding is None:
            return data
        return io.TextIOWrapper(
            io.BytesIO(data), encoding=self._encoding, errors=self._errors
        ).read()


def _ignore_closed_pipe(action) -> None:
    """Run *action*, ignoring the errors ``Popen.communicate()`` ignores when
    the child stopped reading its stdin."""
    try:
        action()
    except BrokenPipeError:
        pass
    except OSError as exc:
        # Windows reports EINVAL when the child exited or closed the pipe.
        if exc.errno != errno.EINVAL:
            raise


def _write_stdin(process: subprocess.Popen, input) -> None:
    """Send *input* and close stdin, as ``Popen.communicate()`` does."""
    if process.stdin is None:
        return
    if input:
        _ignore_closed_pipe(lambda: process.stdin.write(input))
    _ignore_closed_pipe(process.stdin.close)


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

        The call returns when the process exits, not when its output pipes
        reach EOF. A program the command left running (``Start-Process`` with
        ``-RedirectStandardOutput``) inherits the pipes and holds them open
        until it exits; the output the process wrote is read for up to
        ``PIPE_DRAIN_SECONDS`` after it exits (#578).
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
    #
    # CREATE_NO_WINDOW suppresses the console the child would otherwise get.
    # When the server has no console of its own — the usual case when it runs
    # as an MCP extension host — Windows allocates a *new* console for a
    # console child, which flashes on screen and steals keyboard focus from
    # whatever the user is typing in. Redirecting the streams does not prevent
    # the allocation; only this flag does. It composes with the process-group
    # flag, so CTRL_BREAK_EVENT and the graceful-stop path are unaffected.
    creationflags = kwargs.get("creationflags", 0)
    creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    kwargs["creationflags"] = creationflags

    with subprocess.Popen(*popenargs, **kwargs) as process:
        # Wait for the process, not for EOF on its pipes (#578). The readers own
        # the streams from here on and close them at EOF: Popen.__exit__ would
        # otherwise block closing a pipe that a reader is still waiting on.
        readers = []
        for name in ("stdout", "stderr"):
            stream = getattr(process, name)
            readers.append(_PipeReader(stream) if stream is not None else None)
            setattr(process, name, None)

        def collect_output():
            deadline = time.monotonic() + PIPE_DRAIN_SECONDS
            for reader in readers:
                if reader is not None:
                    reader.join(deadline)
            return tuple(reader.output() if reader is not None else None for reader in readers)

        try:
            _write_stdin(process, input)
            process.wait(timeout=timeout)

        except subprocess.TimeoutExpired as exc1:
            # Try graceful shutdown first
            logger.debug('Process did not exit within timeout, attempting graceful shutdown.')
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception:
                logger.debug('Failed to send CTRL_BREAK_EVENT, attempting to terminate process.')

            try:
                process.wait(timeout=grace_period)
                exc1.stdout, exc1.stderr = collect_output()
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
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )

                try:
                    process.wait(timeout=grace_period)
                except subprocess.TimeoutExpired:
                    # Do not replace the original timeout exception
                    pass
                exc2.stdout, exc2.stderr = collect_output()

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
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            raise

        stdout, stderr = collect_output()
        retcode = process.poll()
        if check and retcode:
            raise subprocess.CalledProcessError(
                retcode, process.args, output=stdout, stderr=stderr
            )

        return subprocess.CompletedProcess(process.args, retcode, stdout, stderr)
