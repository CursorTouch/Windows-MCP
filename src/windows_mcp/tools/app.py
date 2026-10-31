"""App tool — launch, resize, switch applications."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from windows_mcp.safety.launch import (
    LaunchDenied,
    ShortcutInfo,
    get_launch_guard,
    resolve_command,
)
from fastmcp import Context


def _as_args(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        args = value
    else:
        args = json.loads(value)
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError("args must be a list of strings")
    return args


def _resolve_executable(executable: str) -> Path:
    """Resolve a path, or a bare name through PATH (never the current directory)."""
    resolved = resolve_command(executable)
    if resolved is None:
        raise ValueError(f"Executable not found (as a path or on PATH): {executable}")
    return Path(resolved)


def _resolve_cwd(cwd: str | None) -> Path | None:
    if cwd is None:
        return None
    path = Path(cwd).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Working directory does not exist: {path}")
    return path


_SHORTCUT_TARGET_SUFFIXES = (".exe", ".com")


def _read_shortcut(path: str) -> ShortcutInfo | None:
    from windows_mcp.safety.win32 import read_shortcut  # Windows only (COM).

    return read_shortcut(path)


def _shortcut_command(
    shortcut: ShortcutInfo, user_args: list[str], user_cwd: Path | None
) -> tuple[str, str, Path | None]:
    """Command line, target and cwd that start *shortcut* the way Explorer would.

    The shortcut's own arguments are a raw Windows command-line fragment, so they are
    appended verbatim; *user_args* are quoted with ``subprocess.list2cmdline``. An explicit
    *user_cwd* wins over the shortcut's working directory.
    """
    target = shortcut.target
    if not target.lower().endswith(_SHORTCUT_TARGET_SUFFIXES):
        raise ValueError(f"Shortcut target is not an executable: {target}")
    if not Path(target).is_file():
        raise ValueError(f"Shortcut target does not exist: {target}")
    parts = [subprocess.list2cmdline([target])]
    if shortcut.arguments.strip():
        parts.append(shortcut.arguments.strip())
    if user_args:
        parts.append(subprocess.list2cmdline(user_args))
    cwd = user_cwd
    if cwd is None and shortcut.working_dir and Path(shortcut.working_dir).is_dir():
        cwd = Path(shortcut.working_dir)
    return " ".join(parts), target, cwd


def _launch_executable(
    executable: str,
    args: list[str] | str | None,
    cwd: str | None,
    *,
    read_shortcut: Callable[[str], ShortcutInfo | None] = _read_shortcut,
    popen: Callable[..., subprocess.Popen] | None = None,
) -> str:
    resolved_executable = _resolve_executable(executable)
    resolved_cwd = _resolve_cwd(cwd)
    resolved_args = _as_args(args)
    guard = get_launch_guard()

    command: list[str] | str = [str(resolved_executable), *resolved_args]
    started = str(resolved_executable)
    shortcut_args = None
    if resolved_executable.suffix.lower() == ".lnk":
        # A .lnk is not an executable for CreateProcess: start its target instead.
        shortcut = read_shortcut(str(resolved_executable))
        if shortcut is None:
            raise ValueError(f"Shortcut has no readable target: {resolved_executable}")
        command, started, resolved_cwd = _shortcut_command(shortcut, resolved_args, resolved_cwd)
        shortcut_args = shortcut.arguments
        if guard is not None:
            # Re-check here: the shortcut may have changed since the gate looked at it.
            error = guard.check_executable(executable, started)
            if error:
                raise LaunchDenied(error)

    if popen is None:
        popen = subprocess.Popen  # Looked up per call so tests can monkeypatch it.
    process = popen(
        command,
        cwd=str(resolved_cwd) if resolved_cwd is not None else None,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    if guard is not None:
        error = guard.verify_launched(executable, process.pid)
        if error:
            raise LaunchDenied(error)
    result = {
        "pid": process.pid,
        "executable": started,
        "args": resolved_args,
        "cwd": str(resolved_cwd) if resolved_cwd is not None else None,
    }
    if shortcut_args is not None:
        result["shortcut"] = str(resolved_executable)
        result["shortcut_args"] = shortcut_args
    return json.dumps(result, indent=2)


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="App",
        description=(
            "Open/start/launch applications and manage windows. Keywords: open, start, launch, program, "
            "application, window, foreground, focus, resize. Four modes: 'launch' (opens an application "
            "by Start Menu name, or an absolute .exe/.lnk path started directly), 'launch_executable' (strictly launches one executable path with separated "
            "argv and optional cwd), 'resize' (adjusts a named or active window), and 'switch' (brings a "
            "specific window into focus). Prefer background execution when supported. If launching an application is known to take focus or disrupt the user, obtain permission first, reusing existing authorization. Relaxed policy does not gate launch modes; this guidance is not server-enforced approval."
        ),
        annotations=ToolAnnotations(
            title="App",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "App-Tool", run_sync_in_thread=False)
    def app_tool(
        mode: Literal["launch", "launch_executable", "resize", "switch"] = "launch",
        name: str | None = None,
        window_loc: list[int] | None = None,
        window_size: list[int] | None = None,
        executable: str | None = None,
        args: list[str] | str | None = None,
        cwd: str | None = None,
        ctx: Context = None,
    ):
        exact_launch_inputs = (executable, args, cwd)
        if mode != "launch_executable" and any(value is not None for value in exact_launch_inputs):
            raise ValueError('executable, args, and cwd require mode="launch_executable"')

        if mode == "launch_executable":
            if executable is None:
                raise ValueError('executable is required for mode="launch_executable"')
            if name is not None or window_loc is not None or window_size is not None:
                raise ValueError(
                    "name, window_loc, and window_size are not supported for "
                    'mode="launch_executable"'
                )
            return _launch_executable(executable, args, cwd)

        return get_desktop().app(mode, name, window_loc, window_size)
