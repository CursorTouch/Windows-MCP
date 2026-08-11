"""App tool — launch, resize, switch applications."""

import json
import subprocess
from pathlib import Path
from typing import Literal

from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
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
    path = Path(executable).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Executable does not exist: {path}")
    return path


def _resolve_cwd(cwd: str | None) -> Path | None:
    if cwd is None:
        return None
    path = Path(cwd).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Working directory does not exist: {path}")
    return path


def _launch_executable(
    executable: str,
    args: list[str] | str | None,
    cwd: str | None,
) -> str:
    resolved_executable = _resolve_executable(executable)
    resolved_cwd = _resolve_cwd(cwd)
    resolved_args = _as_args(args)

    process = subprocess.Popen(
        [str(resolved_executable), *resolved_args],
        cwd=str(resolved_cwd) if resolved_cwd is not None else None,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    return json.dumps(
        {
            "pid": process.pid,
            "executable": str(resolved_executable),
            "args": resolved_args,
            "cwd": str(resolved_cwd) if resolved_cwd is not None else None,
        },
        indent=2,
    )


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="App",
        description=(
            "Open/start/launch applications and manage windows. Keywords: open, start, launch, program, "
            "application, window, foreground, focus, resize. Four modes: 'launch' (opens an application "
            "by Start Menu name), 'launch_executable' (strictly launches one executable path with separated "
            "argv and optional cwd), 'resize' (adjusts a named or active window), and 'switch' (brings a "
            "specific window into focus)."
        ),
        annotations=ToolAnnotations(
            title="App",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "App-Tool")
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
