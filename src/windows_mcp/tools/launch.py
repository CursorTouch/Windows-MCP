"""LaunchExecutable tool - strict non-shell executable launch."""

import json
import subprocess
from pathlib import Path

from fastmcp import Context
from mcp.types import ToolAnnotations

from windows_mcp.infrastructure import with_analytics


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


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="LaunchExecutable",
        description=(
            "Strictly launch one executable path with separated argv and optional cwd. "
            "Does not use a shell, Start Menu search, fuzzy matching, or file associations."
        ),
        annotations=ToolAnnotations(
            title="LaunchExecutable",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "LaunchExecutable-Tool")
    def launch_executable_tool(
        executable: str,
        args: list[str] | str | None = None,
        cwd: str | None = None,
        ctx: Context = None,
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
