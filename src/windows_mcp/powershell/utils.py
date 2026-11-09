import codecs
import errno
import logging
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from typing import IO
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


_CONSOLE_HOSTS = frozenset({"conhost.exe", "openconsole.exe"})
_IMAGE_SUBSYSTEM_WINDOWS_GUI = 2


def _is_gui_program(process: psutil.Process) -> bool:
    """Whether the process runs a GUI-subsystem executable (read from its PE header)."""
    try:
        with open(process.exe(), "rb") as image:
            dos_header = image.read(64)
            if dos_header[:2] != b"MZ":
                return False
            image.seek(int.from_bytes(dos_header[60:64], "little"))
            # PE signature (4 bytes), COFF header (20), then the optional header,
            # whose Subsystem field sits at offset 68 in PE32 and PE32+ alike.
            nt_headers = image.read(4 + 20 + 70)
    except (OSError, psutil.Error):
        return False
    if nt_headers[:4] != b"PE\0\0" or len(nt_headers) < 94:
        return False
    return int.from_bytes(nt_headers[92:94], "little") == _IMAGE_SUBSYSTEM_WINDOWS_GUI


def _runs_on_its_own(process: psutil.Process) -> bool:
    """Whether a program the command started is meant to outlive it.

    Start-Process opens a console program in a console of its own, so it has
    its own console host child. A GUI program has no console at all. A program
    the command runs and waits for shares the command's console and is neither.
    """
    try:
        if process.name().lower() in _CONSOLE_HOSTS:
            return False
        if any(child.name().lower() in _CONSOLE_HOSTS for child in process.children()):
            return True
    except psutil.Error:
        return False
    return _is_gui_program(process)


def kill_command_tree(pid: int) -> None:
    """End a timed-out command and the console programs it runs (#584).

    ``taskkill /T`` also ended the programs the command had started to run on
    their own, such as a GUI program or a ``Start-Process`` console program,
    so a timeout closed a browser or interrupted a build the script had only
    launched. Those keep running now, with all of their descendants. The
    programs that share the command's console, which it was waiting for, end
    with it together with their descendants, as before.
    """
    try:
        root = psutil.Process(pid)
        children = root.children()
    except psutil.NoSuchProcess:
        return
    except psutil.Error:
        logger.debug("Could not list the processes of %d", pid, exc_info=True)
        children = []
    victims = [root]
    for child in children:
        if _runs_on_its_own(child):
            logger.debug("Leaving %d running after the timeout", child.pid)
            continue
        victims.append(child)
        try:
            victims.extend(child.children(recursive=True))
        except psutil.Error:
            pass
    for victim in victims:
        try:
            victim.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            logger.debug("Could not end %d", victim.pid, exc_info=True)


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

    def __init__(self, stream: IO) -> None:
        self._stream = stream
        self._chunks: list[bytes] = []
        self._keep = True
        self._lock = threading.Lock()
        # In text mode, read the bytes and decode at the end, as communicate() does.
        self._encoding = getattr(stream, "encoding", None)
        self._errors = getattr(stream, "errors", None)
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        raw = getattr(self._stream, "buffer", self._stream)
        # An unbuffered pipe (bufsize=0) is a FileIO, which has no read1().
        read = getattr(raw, "read1", raw.read)
        try:
            while chunk := read(65536):
                with self._lock:
                    if self._keep:
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

    def stop_keeping(self) -> None:
        """Keep draining the pipe but drop whatever arrives from now on.

        A program still holding the pipe may write for hours after the call
        has returned. Draining keeps it from blocking on a full pipe.
        """
        with self._lock:
            self._keep = False

    def take_output(self) -> bytes | str:
        """Return what was read, and drop whatever arrives later."""
        with self._lock:
            self._keep = False
            data = b"".join(self._chunks)
            self._chunks = []
        if self._encoding is None:
            return data
        decoder = codecs.getincrementaldecoder(self._encoding)(self._errors or "strict")
        # While another program holds the pipe, the data may end mid-character.
        text = decoder.decode(data, final=not self._thread.is_alive())
        # Universal newlines, as a text-mode pipe gives them.
        return text.replace("\r\n", "\n").replace("\r", "\n")


def _ignore_closed_pipe(action: Callable[[], object]) -> None:
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


def _write_stdin(process: subprocess.Popen, input: bytes | str | None) -> None:
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
           terminate the process and the console programs it runs, with their
           descendants (``kill_command_tree``). Programs it started to run on
           their own (GUI programs, ``Start-Process`` console programs) are
           left running (#584).

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

        def stop_keeping() -> None:
            for reader in readers:
                if reader is not None:
                    reader.stop_keeping()

        def collect_output() -> tuple[bytes | str | None, bytes | str | None]:
            deadline = time.monotonic() + PIPE_DRAIN_SECONDS
            for reader in readers:
                if reader is not None:
                    reader.join(deadline)
            # Stop both before decoding, which may raise.
            stop_keeping()
            stdout, stderr = (r.take_output() if r is not None else None for r in readers)
            return stdout, stderr

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

                # Kill the command and the console programs it runs as a last resort
                logger.debug(
                    f"Process {process.pid} (exist: {check_pid_exists(process.pid)}) did not exit gracefully after {grace_period} seconds, killing it and the console programs it runs..."
                )
                kill_command_tree(process.pid)

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
            # A program left running may hold the pipes; do not store its output.
            stop_keeping()
            kill_command_tree(process.pid)
            raise

        stdout, stderr = collect_output()
        retcode = process.poll()
        if check and retcode:
            raise subprocess.CalledProcessError(
                retcode, process.args, output=stdout, stderr=stderr
            )

        return subprocess.CompletedProcess(process.args, retcode, stdout, stderr)
