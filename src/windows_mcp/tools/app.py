"""App tool — launch applications and manage convenience or exact windows."""

import json
import subprocess
from pathlib import Path
from typing import Literal

from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from fastmcp import Context


AppMode = Literal[
    "launch",
    "launch_executable",
    "resize",
    "switch",
    "find_window",
    "activate_window",
    "set_window_bounds",
]
EXACT_WINDOW_MODES = {"find_window", "activate_window", "set_window_bounds"}
ALL_APP_MODES = {"launch", "launch_executable", "resize", "switch", *EXACT_WINDOW_MODES}


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


def _as_strict_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must contain integers, not booleans")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped.lstrip("+-").isdigit():
            return int(stripped)
    raise ValueError(f"{name} must contain exactly 4 integers")


def _as_optional_positive_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.lstrip("+").isdigit():
            raise ValueError(f"{name} must be a positive integer")
        parsed = int(stripped)
    else:
        raise ValueError(f"{name} must be a positive integer")
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _as_bounds(value: list[int] | str | None, name: str) -> list[int] | None:
    if value is None or isinstance(value, list):
        bounds = value
    elif isinstance(value, str):
        try:
            bounds = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{name} must be a list of exactly 4 integers [x, y, width, height]"
            ) from exc
    else:
        raise ValueError(f"{name} must be a list of exactly 4 integers [x, y, width, height]")
    if bounds is None:
        return None
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError(f"{name} must be a list of exactly 4 integers [x, y, width, height]")
    parsed = [_as_strict_int(item, name) for item in bounds]
    if parsed[2] <= 0 or parsed[3] <= 0:
        raise ValueError(f"{name} width and height must be greater than zero")
    return parsed


def _validate_exact_window_text(
    title: str | None,
    title_match: str,
    process: str | None,
) -> None:
    if title_match not in {"exact", "contains"}:
        raise ValueError('title_match must be "exact" or "contains"')
    if title == "":
        raise ValueError("title must not be empty")
    if process == "":
        raise ValueError("process must not be empty")


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
            "application, window, foreground, focus, resize. Modes: 'launch' and 'launch_executable' "
            "start applications; 'resize' and 'switch' provide convenient name-based window control; "
            "'find_window', 'activate_window', and 'set_window_bounds' use exact native window identity "
            "without fuzzy matching."
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
        mode: AppMode = "launch",
        name: str | None = None,
        window_loc: list[int] | None = None,
        window_size: list[int] | None = None,
        executable: str | None = None,
        args: list[str] | str | None = None,
        cwd: str | None = None,
        title: str | None = None,
        title_match: Literal["exact", "contains"] = "contains",
        process: str | None = None,
        process_id: int | str | None = None,
        handle: int | str | None = None,
        outer: list[int] | str | None = None,
        client: list[int] | str | None = None,
        ctx: Context = None,
    ) -> str | dict[str, object]:
        if mode not in ALL_APP_MODES:
            raise ValueError(
                "mode must be one of: launch, launch_executable, resize, switch, "
                "find_window, activate_window, set_window_bounds"
            )

        exact_launch_inputs = (executable, args, cwd)
        if mode != "launch_executable" and any(value is not None for value in exact_launch_inputs):
            raise ValueError('executable, args, and cwd require mode="launch_executable"')

        exact_window_inputs = (title, process, process_id, handle, outer, client)
        if mode not in EXACT_WINDOW_MODES and (
            any(value is not None for value in exact_window_inputs) or title_match != "contains"
        ):
            raise ValueError(
                "title, title_match, process, process_id, handle, outer, and client require an "
                "exact window mode"
            )

        if mode == "launch_executable":
            if executable is None:
                raise ValueError('executable is required for mode="launch_executable"')
            if name is not None or window_loc is not None or window_size is not None:
                raise ValueError(
                    "name, window_loc, and window_size are not supported for "
                    'mode="launch_executable"'
                )
            return _launch_executable(executable, args, cwd)

        if mode in EXACT_WINDOW_MODES:
            if name is not None or window_loc is not None or window_size is not None:
                raise ValueError(
                    "name, window_loc, and window_size are not supported for exact window modes"
                )

            _validate_exact_window_text(title, title_match, process)
            process_id = _as_optional_positive_int(process_id, "process_id")
            handle = _as_optional_positive_int(handle, "handle")
            outer = _as_bounds(outer, "outer")
            client = _as_bounds(client, "client")

            if mode == "find_window":
                if outer is not None or client is not None:
                    raise ValueError(
                        "outer and client are only supported for mode='set_window_bounds'"
                    )
                windows = get_desktop().find_exact_windows(
                    title=title,
                    title_match=title_match,
                    process=process,
                    process_id=process_id,
                    handle=handle,
                )
                return {"windows": windows, "count": len(windows)}

            if handle is None:
                raise ValueError(f"handle is required for mode='{mode}'")

            if mode == "activate_window":
                if outer is not None or client is not None:
                    raise ValueError(
                        "outer and client are only supported for mode='set_window_bounds'"
                    )
                window = get_desktop().activate_exact_window(
                    handle=handle,
                    process_id=process_id,
                    process=process,
                    title=title,
                    title_match=title_match,
                )
                return {"activated": window}

            if (outer is None) == (client is None):
                raise ValueError("mode='set_window_bounds' requires exactly one of outer or client")
            window = get_desktop().set_exact_window_bounds(
                handle=handle,
                outer=outer,
                client=client,
                process_id=process_id,
                process=process,
                title=title,
                title_match=title_match,
            )
            return {"window": window}

        return get_desktop().app(mode, name, window_loc, window_size)
