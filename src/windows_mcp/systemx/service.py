"""Service layer for advanced Windows system operations.

The functions in this module are deliberately independent of FastMCP. They can
be called from the tool wrappers, tests, or other Python code. Public functions
return human-readable strings; exceptional conditions are raised or returned as
``Error: ...`` strings by the tool layer.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import io
import json
import locale
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from contextlib import contextmanager

from pathlib import Path, PurePosixPath
from typing import Any, Literal, Sequence

import psutil


_WINDOW_ACTIONS = {
    "close",
    "show",
    "hide",
    "minimize",
    "maximize",
    "restore",
    "set_topmost",
    "unset_topmost",
}

_HWND_BROADCAST = 0xFFFF
_WM_CLOSE = 0x0010
_WM_SYSCOMMAND = 0x0112
_WM_INPUTLANGCHANGEREQUEST = 0x0050
_SC_SCREENSAVE = 0xF140
_SW_HIDE = 0
_SW_SHOW = 5
_SW_MINIMIZE = 6
_SW_RESTORE = 9
_SW_MAXIMIZE = 3
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_PROCESS_SUSPEND_RESUME = 0x0800


def _as_bool(value: bool | str | None, default: bool = False) -> bool:
    """Coerce tool arguments that may arrive as strings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _as_int(value: int | str | None, name: str, default: int | None = None) -> int:
    """Coerce tool arguments that may arrive as strings."""
    if value is None:
        if default is not None:
            return default
        raise ValueError(f"{name} is required")
    try:
        return int(str(value).strip(), 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _as_str_list(value: str | Sequence[str] | None) -> list[str]:
    """Parse a list, a JSON list, or newline-separated strings."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON list: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError("Expected a list")
        return [str(item) for item in parsed]
    if "\n" in text:
        return [line.strip() for line in text.splitlines() if line.strip()]
    return [text]


def _resolve_existing_path(value: str, *, directory: bool | None = None) -> Path:
    """Resolve a user path and validate its existence/type."""
    path = Path(str(value)).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")
    if directory is True and not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")
    if directory is False and not path.is_file():
        raise IsADirectoryError(f"Not a file: {path}")
    return path


def _run_powershell(script: str, *, timeout: int = 45) -> str:
    """Run Windows PowerShell without creating a visible console window."""
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if not executable:
        raise RuntimeError("powershell.exe was not found")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=creationflags,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown PowerShell error"
        raise RuntimeError(detail)
    return completed.stdout.strip()


def _run_powershell_json(script: str, *, timeout: int = 45) -> dict[str, Any]:
    """Run PowerShell and parse its JSON output."""
    output = _run_powershell(script, timeout=timeout)
    if not output:
        raise RuntimeError("PowerShell returned no output")
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"PowerShell returned invalid JSON: {output[:300]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("PowerShell returned an unexpected JSON value")
    return parsed


def _window_status(hwnd: int) -> str:
    """Return a compact state description for a top-level window."""
    import win32gui

    if not win32gui.IsWindowVisible(hwnd):
        state = "Hidden"
    elif win32gui.IsIconic(hwnd):
        state = "Minimized"
    elif ctypes.windll.user32.IsZoomed(hwnd):
        state = "Maximized"
    else:
        state = "Normal"
    extras: list[str] = []
    if win32gui.GetForegroundWindow() == hwnd:
        extras.append("Foreground")
    if win32gui.GetWindowLong(hwnd, -20) & 0x00000008:
        extras.append("Topmost")
    if not win32gui.IsWindowEnabled(hwnd):
        extras.append("Disabled")
    return f"{state} ({', '.join(extras)})" if extras else state


def list_windows(
    title_contains: str | None = None,
    include_hidden: bool | str = False,
    limit: int | str = 200,
) -> str:
    """List top-level windows with title, class, PID, handle, and state."""
    import win32gui
    import win32process

    include_hidden = _as_bool(include_hidden)
    max_items = max(1, min(_as_int(limit, "limit", 200), 2000))
    needle = title_contains.casefold() if title_contains else None
    rows: list[list[Any]] = []

    def callback(hwnd: int, _extra: Any) -> bool:
        if not win32gui.IsWindow(hwnd):
            return True
        visible = win32gui.IsWindowVisible(hwnd)
        if not visible and not include_hidden:
            return True
        title = win32gui.GetWindowText(hwnd) or "<untitled>"
        if needle and needle not in title.casefold():
            return True
        try:
            class_name = win32gui.GetClassName(hwnd)
        except Exception:
            class_name = "<unknown>"
        try:
            _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        rows.append([title, class_name, pid, hwnd, _window_status(hwnd)])
        return True

    win32gui.EnumWindows(callback, None)
    rows.sort(key=lambda row: (str(row[0]).casefold(), int(row[3])))
    total = len(rows)
    rows = rows[:max_items]
    if not rows:
        return "No matching windows found."
    from tabulate import tabulate

    table = tabulate(
        rows,
        headers=["Title", "Class", "PID", "Handle", "Status"],
        tablefmt="simple",
        disable_numparse=True,
    )
    suffix = f" (showing {len(rows)} of {total})" if total > len(rows) else ""
    return f"Windows{suffix}:\n{table}"


def _enumerate_window_handles() -> list[int]:
    import win32gui

    handles: list[int] = []

    def callback(hwnd: int, _extra: Any) -> bool:
        if win32gui.IsWindow(hwnd):
            handles.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    return handles


def _resolve_window(
    handle: int | str | None = None,
    title: str | None = None,
    pid: int | str | None = None,
) -> int:
    import win32gui
    import win32process

    if handle is not None:
        hwnd = _as_int(handle, "handle")
        if not win32gui.IsWindow(hwnd):
            raise ValueError(f"Window handle is not valid: {hwnd}")
        return hwnd

    pid_value = _as_int(pid, "pid") if pid is not None else None
    title_needle = title.casefold().strip() if title else None
    candidates: list[tuple[int, str, int]] = []
    for hwnd in _enumerate_window_handles():
        try:
            window_title = win32gui.GetWindowText(hwnd) or ""
            _thread_id, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            import win32con

            owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
        except Exception:
            continue
        if pid_value is not None and window_pid != pid_value:
            continue
        if pid_value is not None and owner and not title_needle:
            continue
        if title_needle and not (
            window_title.casefold() == title_needle or title_needle in window_title.casefold()
        ):
            continue
        if window_title:
            candidates.append((hwnd, window_title, window_pid))
    if not candidates:
        raise LookupError("No matching window found")
    exact = [item for item in candidates if item[1].casefold() == title_needle]
    foreground = win32gui.GetForegroundWindow()
    if exact:
        if len(exact) > 1:
            raise LookupError(
                "Multiple windows match that title; provide handle or pid: "
                + ", ".join(f"{item[1]} ({item[0]})" for item in exact[:8])
            )
        return exact[0][0]
    if len(candidates) > 1:
        raise LookupError(
            "Multiple windows match; provide handle or pid: "
            + ", ".join(f"{item[1]} ({item[0]})" for item in candidates[:8])
        )
    if foreground in [item[0] for item in candidates]:
        return foreground
    return candidates[0][0]


def control_window(
    action: Literal[
        "close",
        "show",
        "hide",
        "minimize",
        "maximize",
        "restore",
        "set_topmost",
        "unset_topmost",
    ],
    handle: int | str | None = None,
    title: str | None = None,
    pid: int | str | None = None,
) -> str:
    """Change a window state.

    ``close`` posts WM_CLOSE and may discard unsaved application data.
    """
    import win32con
    import win32gui

    if action not in _WINDOW_ACTIONS:
        raise ValueError(f"Unsupported window action: {action}")
    hwnd = _resolve_window(handle=handle, title=title, pid=pid)
    window_title = win32gui.GetWindowText(hwnd) or f"0x{hwnd:X}"
    if action == "close":
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return f"Close request sent to '{window_title}' (handle {hwnd})."
    if action == "show":
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    elif action == "hide":
        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
    elif action == "minimize":
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    elif action == "maximize":
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    elif action == "restore":
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    elif action == "set_topmost":
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )
    elif action == "unset_topmost":
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )
    return f"Window '{window_title}' action '{action}' applied (handle {hwnd})."


@contextmanager
def _clipboard_open(retries: int = 10, delay: float = 0.08):
    """Open the clipboard with retries for transient sharing violations."""
    import win32clipboard

    last_error: Exception | None = None
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            win32clipboard.OpenClipboard(None)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay * (attempt + 1))
            continue
        try:
            yield
        finally:
            win32clipboard.CloseClipboard()
        return
    raise RuntimeError(f"Could not open clipboard after {attempts} attempts: {last_error}")


def clipboard_get_text() -> str:
    """Read Unicode text from the clipboard."""
    import win32clipboard

    with _clipboard_open():
        if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            return "Clipboard does not contain Unicode text."
        data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    return f"Clipboard text:\n{data}"


def clipboard_set_text(text: str) -> str:
    """Replace the clipboard contents with Unicode text."""
    import win32clipboard

    with _clipboard_open():
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(str(text), win32clipboard.CF_UNICODETEXT)
    return f"Clipboard text set ({len(str(text))} characters)."


def clipboard_set_image(image_path: str) -> str:
    """Replace the clipboard contents with a DIB image loaded from ``image_path``."""
    from PIL import Image
    import win32clipboard

    path = _resolve_existing_path(image_path, directory=False)
    with Image.open(path) as image:
        with io.BytesIO() as output:
            image.convert("RGB").save(output, format="BMP")
            dib = output.getvalue()[14:]
    with _clipboard_open():
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
    return f"Clipboard image set from {path}."


def clipboard_get_image(output_path: str | None = None) -> str:
    """Read a CF_DIB clipboard image and optionally save it as an image file."""
    from PIL import Image
    import win32clipboard

    with _clipboard_open():
        if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB):
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_BITMAP):
                return "Clipboard contains a bitmap handle (CF_BITMAP), not a CF_DIB image."
            return "Clipboard does not contain an image."
        dib = win32clipboard.GetClipboardData(win32clipboard.CF_DIB)
    if isinstance(dib, str):
        dib = dib.encode("latin-1")
    if not isinstance(dib, (bytes, bytearray)) or len(dib) < 12:
        raise RuntimeError("Clipboard returned invalid DIB image data")
    dib = bytes(dib)
    header_size = struct.unpack_from("<I", dib, 0)[0]
    file_size = 14 + len(dib)
    bmp = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, 14 + header_size) + dib
    with Image.open(io.BytesIO(bmp)) as image:
        image.load()
        if output_path:
            path = Path(str(output_path)).expanduser().resolve()
        else:
            path = (
                Path(tempfile.gettempdir()) / f"windows_mcp_clipboard_{int(time.time() * 1000)}.png"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.suffix:
            path = path.with_suffix(".png")
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            image.convert("RGB").save(path, quality=95)
        else:
            image.save(path)
        return f"Clipboard image ({image.width}x{image.height}) saved to {path}."


def clipboard_set_files(paths: str | Sequence[str]) -> str:
    """Replace the clipboard contents with one or more file paths (CF_HDROP)."""
    import win32clipboard

    resolved = [_resolve_existing_path(item) for item in _as_str_list(paths)]
    if not resolved:
        raise ValueError("At least one path is required")
    dropfiles = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    payload = dropfiles + ("\0".join(str(path) for path in resolved) + "\0\0").encode("utf-16-le")
    with _clipboard_open():
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_HDROP, payload)
    return f"Clipboard file list set ({len(resolved)} item(s))."


def clipboard_get_files() -> str:
    """Read a CF_HDROP file list from the clipboard."""
    import win32clipboard

    with _clipboard_open():
        if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
            return "Clipboard does not contain a file list."
        data = win32clipboard.GetClipboardData(win32clipboard.CF_HDROP)
    if isinstance(data, str):
        files = [data]
    else:
        files = [str(item) for item in data]
    return "Clipboard files:\n" + "\n".join(files)


def _clipboard_html_payload(html: str) -> bytes:
    """Build a CF_HTML payload with correctly calculated byte offsets."""
    fragment = str(html)
    prefix = "<html><body><!--StartFragment-->"
    suffix = "<!--EndFragment--></body></html>"
    document = prefix + fragment + suffix
    template = (
        "Version:0.9\r\n"
        "StartHTML:{start_html:010d}\r\n"
        "EndHTML:{end_html:010d}\r\n"
        "StartFragment:{start_fragment:010d}\r\n"
        "EndFragment:{end_fragment:010d}\r\n"
    )
    placeholder = template.format(start_html=0, end_html=0, start_fragment=0, end_fragment=0)
    start_html = len(placeholder.encode("utf-8"))
    start_fragment = start_html + len(prefix.encode("utf-8"))
    end_fragment = start_fragment + len(fragment.encode("utf-8"))
    end_html = start_html + len(document.encode("utf-8"))
    header = template.format(
        start_html=start_html,
        end_html=end_html,
        start_fragment=start_fragment,
        end_fragment=end_fragment,
    )
    return (header + document).encode("utf-8")


def clipboard_set_html(html: str) -> str:
    """Replace the clipboard contents with an HTML fragment."""
    import win32clipboard

    with _clipboard_open():
        win32clipboard.EmptyClipboard()
        fmt = win32clipboard.RegisterClipboardFormat("HTML Format")
        win32clipboard.SetClipboardData(fmt, _clipboard_html_payload(html))
    return f"Clipboard HTML set ({len(str(html))} characters)."


def clipboard_get_html() -> str:
    """Read the HTML fragment from the clipboard."""
    import win32clipboard

    with _clipboard_open():
        fmt = win32clipboard.RegisterClipboardFormat("HTML Format")
        if not win32clipboard.IsClipboardFormatAvailable(fmt):
            return "Clipboard does not contain HTML."
        data = win32clipboard.GetClipboardData(fmt)
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
    else:
        text = str(data)
    match = re.search(r"StartFragment:\s*(\d+)", text, flags=re.IGNORECASE)
    end_match = re.search(r"EndFragment:\s*(\d+)", text, flags=re.IGNORECASE)
    if match and end_match:
        start = int(match.group(1))
        end = int(end_match.group(1))
        raw = text.encode("utf-8", errors="replace")
        fragment = raw[start:end].decode("utf-8", errors="replace")
        return f"Clipboard HTML:\n{fragment}"
    return f"Clipboard HTML:\n{text}"


def clipboard_clear() -> str:
    """Clear all clipboard formats."""
    with _clipboard_open():
        import win32clipboard

        win32clipboard.EmptyClipboard()
    return "Clipboard cleared."


def clipboard_formats() -> str:
    """List the formats currently available on the clipboard."""
    import win32clipboard
    from tabulate import tabulate

    names = {
        1: "CF_TEXT",
        2: "CF_BITMAP",
        3: "CF_METAFILEPICT",
        4: "CF_SYLK",
        5: "CF_DIF",
        6: "CF_TIFF",
        7: "CF_OEMTEXT",
        8: "CF_DIB",
        9: "CF_PALETTE",
        10: "CF_PENDATA",
        11: "CF_RIFF",
        12: "CF_WAVE",
        13: "CF_UNICODETEXT",
        14: "CF_ENHMETAFILE",
        15: "CF_HDROP",
        16: "CF_LOCALE",
        17: "CF_DIBV5",
    }
    rows: list[list[Any]] = []
    with _clipboard_open():
        fmt = 0
        while True:
            fmt = win32clipboard.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in names:
                name = names[fmt]
            else:
                try:
                    name = win32clipboard.GetClipboardFormatName(fmt)
                except Exception:
                    name = f"Format-{fmt}"
            rows.append([fmt, name])
    if not rows:
        return "Clipboard is empty."
    return "Clipboard formats:\n" + tabulate(rows, headers=["ID", "Format"], tablefmt="simple")


def _archive_format(archive_path: Path, archive_format: str) -> str:
    """Resolve an archive format name from its explicit name or suffix."""
    requested = str(archive_format or "auto").strip().lower()
    if requested == "auto":
        lower = archive_path.name.lower()
        if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
            return "tar.gz"
        if lower.endswith(".zip"):
            return "zip"
        if lower.endswith(".tar"):
            return "tar"
        raise ValueError("Cannot infer archive format; use zip, tar, or tar.gz")
    aliases = {"targz": "tar.gz", "gz": "tar.gz", "tgz": "tar.gz"}
    requested = aliases.get(requested, requested)
    if requested not in {"zip", "tar", "tar.gz"}:
        raise ValueError("archive_format must be zip, tar, tar.gz, or auto")
    return requested


def _unique_archive_name(name: str, seen: set[str]) -> str:
    """Return a deterministic unique archive member name."""
    if name not in seen:
        seen.add(name)
        return name
    path = PurePosixPath(name)
    suffix = "".join(path.suffixes)
    stem = name[: -len(suffix)] if suffix else name
    counter = 2
    candidate = f"{stem}_{counter}{suffix}"
    while candidate in seen:
        counter += 1
        candidate = f"{stem}_{counter}{suffix}"
    seen.add(candidate)
    return candidate


def _iter_source_files(source: Path, destination: Path) -> list[tuple[Path, str]]:
    """Return (physical path, archive member name) pairs for a source path."""
    if source.is_file():
        return [(source, source.name)]
    files: list[tuple[Path, str]] = []
    for root, _dirs, names in os.walk(source):
        for name in names:
            current = Path(root) / name
            if current.resolve() == destination:
                continue
            member = (Path(root).relative_to(source.parent) / name).as_posix()
            files.append((current, member))
    return files


def create_archive(
    sources: str | Sequence[str],
    archive_path: str,
    archive_format: str = "auto",
    overwrite: bool | str = False,
) -> str:
    """Create a zip, tar, or tar.gz archive from one or more files/directories.

    Directory sources are stored under their own base directory. Existing
    archives are replaced only when ``overwrite`` is true.
    """
    source_values = _as_str_list(sources)
    if not source_values:
        raise ValueError("At least one source path is required")
    resolved_sources = [_resolve_existing_path(item) for item in source_values]
    destination = Path(str(archive_path)).expanduser().resolve()
    if destination.exists() and not _as_bool(overwrite):
        raise FileExistsError(f"Archive already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fmt = _archive_format(destination, archive_format)

    members: list[tuple[Path, str]] = []
    for source in resolved_sources:
        members.extend(_iter_source_files(source, destination))
    if not members:
        raise ValueError("No files found in the selected source paths")

    seen: set[str] = set()
    if fmt == "zip":
        with zipfile.ZipFile(
            destination,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
            strict_timestamps=False,
        ) as archive:
            for physical, member in members:
                member = member.replace("\\", "/")
                archive.write(physical, _unique_archive_name(member, seen))
    else:
        mode = "w:gz" if fmt == "tar.gz" else "w"
        with tarfile.open(destination, mode) as archive:
            for physical, member in members:
                member = member.replace("\\", "/")
                archive.add(physical, arcname=_unique_archive_name(member, seen), recursive=False)
    return f"Created {fmt} archive {destination} with {len(members)} file(s)."


def _safe_extract_path(destination: Path, member_name: str) -> Path:
    """Resolve an archive member while preventing zip-slip/path traversal."""
    normalized = member_name.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"Unsafe absolute archive member: {member_name}")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts or any(":" in part for part in pure.parts):
        raise ValueError(f"Unsafe archive member: {member_name}")
    target = (destination / Path(*pure.parts)).resolve()
    destination_resolved = destination.resolve()
    if target != destination_resolved and destination_resolved not in target.parents:
        raise ValueError(f"Archive member escapes destination: {member_name}")
    return target


def extract_archive(
    archive_path: str,
    output_dir: str,
    overwrite: bool | str = False,
) -> str:
    """Extract a zip/tar/tar.gz archive with path traversal protection."""
    archive_file = _resolve_existing_path(archive_path, directory=False)
    destination = Path(str(output_dir)).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    overwrite_value = _as_bool(overwrite)
    extracted: list[str] = []

    if zipfile.is_zipfile(archive_file):
        with zipfile.ZipFile(archive_file) as archive:
            for info in archive.infolist():
                target = _safe_extract_path(destination, info.filename)
                mode = (info.external_attr >> 16) & 0xF000
                if mode == 0xA000:
                    raise ValueError(f"Archive contains a symbolic link: {info.filename}")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if target.exists() and not overwrite_value:
                    raise FileExistsError(f"Extraction target already exists: {target}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                extracted.append(str(target))
    elif tarfile.is_tarfile(archive_file):
        with tarfile.open(archive_file, "r:*") as archive:
            for member in archive.getmembers():
                if member.issym() or member.islnk():
                    raise ValueError(f"Archive contains a link: {member.name}")
                if not (member.isfile() or member.isdir()):
                    raise ValueError(f"Archive contains an unsupported member: {member.name}")
                target = _safe_extract_path(destination, member.name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if target.exists() and not overwrite_value:
                    raise FileExistsError(f"Extraction target already exists: {target}")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise RuntimeError(f"Could not read archive member: {member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                extracted.append(str(target))
    else:
        raise ValueError(f"Unsupported or invalid archive: {archive_file}")
    return f"Extracted {len(extracted)} file(s) from {archive_file} to {destination}."


def list_archive(archive_path: str) -> str:
    """List the members and sizes in a zip/tar/tar.gz archive."""
    from tabulate import tabulate

    archive_file = _resolve_existing_path(archive_path, directory=False)
    rows: list[list[Any]] = []
    if zipfile.is_zipfile(archive_file):
        with zipfile.ZipFile(archive_file) as archive:
            for info in archive.infolist():
                item_type = "Directory" if info.is_dir() else "File"
                rows.append([info.filename, item_type, info.file_size, info.date_time])
    elif tarfile.is_tarfile(archive_file):
        with tarfile.open(archive_file, "r:*") as archive:
            for member in archive.getmembers():
                item_type = "Directory" if member.isdir() else "File"
                rows.append([member.name, item_type, member.size, member.mtime])
    else:
        raise ValueError(f"Unsupported or invalid archive: {archive_file}")
    if not rows:
        return f"Archive is empty: {archive_file}"
    return f"Archive contents ({archive_file}):\n" + tabulate(
        rows, headers=["Name", "Type", "Size", "Modified"], tablefmt="simple"
    )


_VOLUME_SCRIPT = r"""
$ErrorActionPreference = "Stop"
$code = @"
using System;
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
 [PreserveSig] int RegisterControlChangeNotify(IntPtr pNotify);
 [PreserveSig] int UnregisterControlChangeNotify(IntPtr pNotify);
 [PreserveSig] int GetChannelCount(out int pnChannelCount);
 [PreserveSig] int SetMasterVolumeLevel(float fLevelDB, Guid pguidEventContext);
 [PreserveSig] int SetMasterVolumeLevelScalar(float fLevel, Guid pguidEventContext);
 [PreserveSig] int GetMasterVolumeLevel(out float pfLevelDB);
 [PreserveSig] int GetMasterVolumeLevelScalar(out float pfLevel);
 [PreserveSig] int SetChannelVolumeLevel(uint nChannel, float fLevelDB, Guid pguidEventContext);
 [PreserveSig] int SetChannelVolumeLevelScalar(uint nChannel, float fLevel, Guid pguidEventContext);
 [PreserveSig] int GetChannelVolumeLevel(uint nChannel, out float pfLevelDB);
 [PreserveSig] int GetChannelVolumeLevelScalar(uint nChannel, out float pfLevel);
 [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, Guid pguidEventContext);
 [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool pbMute);
 [PreserveSig] int GetVolumeStepInfo(out uint pnStep, out uint pnStepCount);
 [PreserveSig] int VolumeStepUp(Guid pguidEventContext);
 [PreserveSig] int VolumeStepDown(Guid pguidEventContext);
 [PreserveSig] int QueryHardwareSupport(out uint pdwHardwareSupportMask);
 [PreserveSig] int GetVolumeRange(out float pflVolumeMindB, out float pflVolumeMaxdB, out float pflVolumeIncrementdB);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
 [PreserveSig] int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams, [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
 [PreserveSig] int OpenPropertyStore(int stgmAccess, out IntPtr ppProperties);
 [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string ppstrId);
 [PreserveSig] int GetState(out int pdwState);
}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
 [PreserveSig] int EnumAudioEndpoints(int dataFlow, int dwStateMask, out IntPtr ppDevices);
 [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppEndpoint);
 [PreserveSig] int GetDevice(string pwstrId, out IMMDevice ppDevice);
 [PreserveSig] int RegisterEndpointNotificationCallback(IntPtr pClient);
 [PreserveSig] int UnregisterEndpointNotificationCallback(IntPtr pClient);
}
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
class MMDeviceEnumeratorComObject { }
public static class Audio {
 static IAudioEndpointVolume GetVolumeObject() {
   var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
   IMMDevice device;
   Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(0, 1, out device));
   Guid iid = typeof(IAudioEndpointVolume).GUID;
   object o;
   Marshal.ThrowExceptionForHR(device.Activate(ref iid, 23, IntPtr.Zero, out o));
   return (IAudioEndpointVolume)o;
 }
 public static float GetVolume() { float v; Marshal.ThrowExceptionForHR(GetVolumeObject().GetMasterVolumeLevelScalar(out v)); return v; }
 public static void SetVolume(float v) { Marshal.ThrowExceptionForHR(GetVolumeObject().SetMasterVolumeLevelScalar(v, Guid.Empty)); }
 public static bool GetMute() { bool m; Marshal.ThrowExceptionForHR(GetVolumeObject().GetMute(out m)); return m; }
 public static void SetMute(bool m) { Marshal.ThrowExceptionForHR(GetVolumeObject().SetMute(m, Guid.Empty)); }
}
"@
Add-Type -TypeDefinition $code
"""

# PowerShell volume getters return compact JSON. The Core Audio helper is
# compiled on demand because pycaw is not a project dependency.
_VOLUME_GET_SCRIPT = (
    _VOLUME_SCRIPT
    + r"""
$level = [Audio]::GetVolume()
[pscustomobject]@{ volume = [math]::Round($level * 100, 0); mute = [Audio]::GetMute() } | ConvertTo-Json -Compress
"""
)


def get_volume() -> str:
    """Return the current default playback endpoint volume and mute state."""
    result = _run_powershell_json(_VOLUME_GET_SCRIPT, timeout=60)
    return json.dumps(result, ensure_ascii=False, indent=2)


def set_volume(level: int | str) -> str:
    """Set the default playback endpoint volume to a percentage from 0 to 100."""
    value = max(0, min(_as_int(level, "level"), 100))
    script = _VOLUME_SCRIPT + f"$v = {value} / 100.0; [Audio]::SetVolume([single]$v)\n"
    _run_powershell(script, timeout=60)
    return f"Volume set to {value}%."


def get_volume_mute() -> str:
    """Return the current default playback endpoint mute state."""
    result = _run_powershell_json(_VOLUME_GET_SCRIPT, timeout=60)
    return f"Mute: {'on' if bool(result.get('mute')) else 'off'}"


def set_volume_mute(mute: bool | str) -> str:
    """Mute or unmute the default playback endpoint."""
    enabled = _as_bool(mute)
    literal = "$true" if enabled else "$false"
    script = _VOLUME_SCRIPT + f"[Audio]::SetMute({literal})\n"
    _run_powershell(script, timeout=60)
    return f"Mute {'enabled' if enabled else 'disabled'}."


def get_brightness() -> str:
    """Return monitor brightness values exposed by the WMI monitor provider."""
    script = r"""
$ErrorActionPreference = "Stop"
$values = @()
try {
  $values = @(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop |
    Select-Object -ExpandProperty CurrentBrightness)
} catch {
  $values = @()
}
if ($values.Count -eq 0) {
  [pscustomobject]@{ supported = $false; values = @() } | ConvertTo-Json -Compress
} else {
  [pscustomobject]@{ supported = $true; values = @($values) } | ConvertTo-Json -Compress
}
"""
    result = _run_powershell_json(script, timeout=45)
    if not result.get("supported"):
        return "Brightness control is not supported on this display or device."
    values = result.get("values") or []
    return f"Brightness: {', '.join(str(value) + '%' for value in values)}"


def set_brightness(level: int | str) -> str:
    """Set the laptop display brightness; fails on most desktops/external displays."""
    value = max(0, min(_as_int(level, "level"), 100))
    script = rf"""
$ErrorActionPreference = "Stop"
$methods = @(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop)
if ($methods.Count -eq 0) {{ throw "No WMI brightness method is available on this device." }}
$result = Invoke-CimMethod -InputObject $methods[0] -MethodName WmiSetBrightness -Arguments @{{ Timeout = [uint32]1; Brightness = [byte]{value} }}
if ($result.ReturnValue -ne 0) {{ throw "WmiSetBrightness returned $($result.ReturnValue)." }}
"""
    _run_powershell(script, timeout=45)
    return f"Brightness set to {value}%."


def lock_workstation(dry_run: bool | str = False) -> str:
    """Lock the current Windows workstation.

    The function is intentionally annotated as destructive by the tool layer:
    locking can interrupt the user's active desktop session.
    """
    if _as_bool(dry_run):
        return "Dry run: LockWorkStation would lock the current session."
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if not user32.LockWorkStation():
        error = ctypes.get_last_error()
        raise OSError(error, "LockWorkStation failed")
    return "Workstation lock requested."


def start_screensaver(dry_run: bool | str = False) -> str:
    """Ask Windows to start the configured screen saver."""
    if _as_bool(dry_run):
        return "Dry run: WM_SYSCOMMAND/SC_SCREENSAVE would be broadcast."
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendMessageW(
        wintypes.HWND(_HWND_BROADCAST),
        wintypes.UINT(_WM_SYSCOMMAND),
        wintypes.WPARAM(_SC_SCREENSAVE),
        wintypes.LPARAM(0),
    )
    return "Screen saver start request broadcast."

# ---------------------------------------------------------------------------
# Power state control
#
# Every entry point below ends the user's session or powers the machine down.
# The tool layer marks them destructive, and each one accepts ``dry_run`` so an
# agent can rehearse the call before committing to it.
# ---------------------------------------------------------------------------

_EWX_LOGOFF = 0x00000000
_EWX_FORCE = 0x00000004
_EWX_FORCEIFHUNG = 0x00000010

_SE_SHUTDOWN_NAME = "SeShutdownPrivilege"
_TOKEN_ADJUST_PRIVILEGES = 0x0020
_TOKEN_QUERY = 0x0008
_SE_PRIVILEGE_ENABLED = 0x0002
_ERROR_NOT_ALL_ASSIGNED = 1300

# SHTDN_REASON_* triple: application-initiated, maintenance, planned.
_SHUTDOWN_REASON = 0x00040000 | 0x00000003 | 0x80000000


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _LUIDAndAttributes(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]


class _TokenPrivileges(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", _LUIDAndAttributes * 1),
    ]


def _enable_shutdown_privilege() -> None:
    """Enable ``SeShutdownPrivilege`` for the current process.

    A normal user token is *granted* this privilege but does not have it
    *enabled*, so shutdown/logoff calls fail with ERROR_PRIVILEGE_NOT_HELD until
    it is switched on explicitly.
    """
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        _TOKEN_ADJUST_PRIVILEGES | _TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    try:
        luid = _LUID()
        if not advapi32.LookupPrivilegeValueW(None, _SE_SHUTDOWN_NAME, ctypes.byref(luid)):
            raise OSError(
                ctypes.get_last_error(), "LookupPrivilegeValue(SeShutdownPrivilege) failed"
            )
        privileges = _TokenPrivileges(
            PrivilegeCount=1,
            Privileges=(_LUIDAndAttributes * 1)(
                _LUIDAndAttributes(Luid=luid, Attributes=_SE_PRIVILEGE_ENABLED)
            ),
        )
        if not advapi32.AdjustTokenPrivileges(
            token, False, ctypes.byref(privileges), 0, None, None
        ):
            raise OSError(ctypes.get_last_error(), "AdjustTokenPrivileges failed")
        # AdjustTokenPrivileges can report success while granting nothing.
        if ctypes.get_last_error() == _ERROR_NOT_ALL_ASSIGNED:
            raise OSError(
                _ERROR_NOT_ALL_ASSIGNED,
                "SeShutdownPrivilege is not held by this process",
            )
    finally:
        kernel32.CloseHandle(token)


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("Power control is only available on Windows.")


def _initiate_shutdown(
    *,
    delay: int,
    force: bool,
    message: str | None,
    reboot: bool,
) -> None:
    """Call ``InitiateSystemShutdownExW`` after enabling the needed privilege."""
    _enable_shutdown_privilege()
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.InitiateSystemShutdownExW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    advapi32.InitiateSystemShutdownExW.restype = wintypes.BOOL
    ok = advapi32.InitiateSystemShutdownExW(
        None,  # local machine
        message,
        wintypes.DWORD(delay),
        force,
        reboot,
        wintypes.DWORD(_SHUTDOWN_REASON),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "InitiateSystemShutdownExW failed")


def shutdown(
    *,
    timeout: int | str = 0,
    force: bool | str = False,
    message: str | None = None,
    dry_run: bool | str = False,
) -> str:
    """Shut the machine down, optionally after a grace period.

    ``timeout`` is the number of seconds before the machine powers off; call
    :func:`abort_shutdown` during that window to cancel. ``force`` closes
    applications that do not respond instead of waiting for them.
    """
    _require_windows()
    delay = max(0, _as_int(timeout, "timeout"))
    force_value = _as_bool(force)
    if _as_bool(dry_run):
        return (
            f"Dry run: the machine would shut down in {delay}s"
            f"{' with applications forced closed' if force_value else ''}."
        )
    _initiate_shutdown(delay=delay, force=force_value, message=message, reboot=False)
    if delay:
        return f"Shutdown scheduled in {delay}s; call mode='abort' to cancel it."
    return "Shutdown initiated."


def restart(
    *,
    timeout: int | str = 0,
    force: bool | str = False,
    message: str | None = None,
    dry_run: bool | str = False,
) -> str:
    """Restart the machine, optionally after a grace period."""
    _require_windows()
    delay = max(0, _as_int(timeout, "timeout"))
    force_value = _as_bool(force)
    if _as_bool(dry_run):
        return (
            f"Dry run: the machine would restart in {delay}s"
            f"{' with applications forced closed' if force_value else ''}."
        )
    _initiate_shutdown(delay=delay, force=force_value, message=message, reboot=True)
    if delay:
        return f"Restart scheduled in {delay}s; call mode='abort' to cancel it."
    return "Restart initiated."


def logoff(*, force: bool | str = False, dry_run: bool | str = False) -> str:
    """Log the interactive user off; ``force`` closes blocking applications."""
    _require_windows()
    force_value = _as_bool(force)
    if _as_bool(dry_run):
        return "Dry run: the interactive session would be logged off."
    _enable_shutdown_privilege()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    flags = _EWX_LOGOFF | (_EWX_FORCE if force_value else _EWX_FORCEIFHUNG)
    if not user32.ExitWindowsEx(wintypes.UINT(flags), wintypes.DWORD(_SHUTDOWN_REASON)):
        raise OSError(ctypes.get_last_error(), "ExitWindowsEx failed")
    return "Logoff initiated."


def suspend(
    *,
    hibernate: bool | str = False,
    force: bool | str = False,
    dry_run: bool | str = False,
) -> str:
    """Suspend the machine to RAM, or hibernate to disk when ``hibernate`` is set."""
    _require_windows()
    hibernate_value = _as_bool(hibernate)
    force_value = _as_bool(force)
    target = "hibernate" if hibernate_value else "sleep"
    if _as_bool(dry_run):
        return f"Dry run: the machine would {target}."
    powrprof = ctypes.WinDLL("powrprof", use_last_error=True)
    powrprof.SetSuspendState.argtypes = [wintypes.BOOL, wintypes.BOOL, wintypes.BOOL]
    powrprof.SetSuspendState.restype = wintypes.BOOL
    if not powrprof.SetSuspendState(hibernate_value, force_value, False):
        raise OSError(
            ctypes.get_last_error(),
            "SetSuspendState failed; hibernation may be disabled on this machine",
        )
    return f"{'Hibernate' if hibernate_value else 'Sleep'} requested."


def abort_shutdown(*, dry_run: bool | str = False) -> str:
    """Cancel a pending shutdown or restart started with a grace period."""
    _require_windows()
    if _as_bool(dry_run):
        return "Dry run: a pending shutdown would be aborted."
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.AbortSystemShutdownW.argtypes = [wintypes.LPCWSTR]
    advapi32.AbortSystemShutdownW.restype = wintypes.BOOL
    if not advapi32.AbortSystemShutdownW(None):
        raise OSError(ctypes.get_last_error(), "AbortSystemShutdownW failed")
    return "Pending shutdown aborted."



def _keyboard_layout_rows() -> list[dict[str, Any]]:
    """Return keyboard layouts installed for the current user/session."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    count = user32.GetKeyboardLayoutList(0, None)
    if count <= 0:
        return []
    layouts = (ctypes.c_void_p * count)()
    user32.GetKeyboardLayoutList(count, layouts)
    rows: list[dict[str, Any]] = []
    for handle in layouts:
        if not handle:
            continue
        buffer = ctypes.create_unicode_buffer(16)
        if not user32.GetKeyboardLayoutNameW(buffer):
            continue
        layout_id = buffer.value.upper()
        language_id = int(layout_id, 16) & 0xFFFF
        language = locale.windows_locale.get(language_id, f"LCID-{language_id:04X}")
        rows.append({"handle": int(handle), "layout_id": layout_id, "language": language})
    return rows


def list_keyboard_layouts() -> str:
    """List keyboard layouts available to the current Windows session."""
    from tabulate import tabulate

    rows = _keyboard_layout_rows()
    if not rows:
        return "No keyboard layouts reported by the current session."
    table = tabulate(
        [[row["handle"], row["layout_id"], row["language"]] for row in rows],
        headers=["HKL", "Layout ID", "Language"],
        tablefmt="simple",
    )
    return f"Keyboard layouts:\n{table}"


def switch_keyboard_layout(layout: str | int, handle: int | str | None = None) -> str:
    """Switch the input language for a target window (or the foreground window)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
    user32.ActivateKeyboardLayout.restype = ctypes.c_void_p
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    raw = str(layout).strip()
    rows = _keyboard_layout_rows()
    selected: int | None = None
    if re.fullmatch(r"0[xX][0-9A-Fa-f]{8}", raw):
        selected = int(raw, 16)
    elif re.fullmatch(r"\d+", raw):
        value = int(raw)
        exact = [
            row for row in rows if row["handle"] == value or int(row["layout_id"], 16) == value
        ]
        selected = exact[0]["handle"] if exact else value
    else:
        matches = [
            row
            for row in rows
            if raw.casefold() in str(row["language"]).casefold()
            or raw.casefold() in str(row["layout_id"]).casefold()
        ]
        if matches:
            selected = int(matches[0]["handle"])
    if selected is None:
        raise ValueError(f"Keyboard layout not found: {layout}")
    if re.fullmatch(r"0[xX][0-9A-Fa-f]{8}", raw):
        loaded = user32.LoadKeyboardLayoutW(ctypes.c_wchar_p(raw), 0x00000001 | 0x00000100)
        if loaded:
            selected = int(loaded)
    user32.ActivateKeyboardLayout(ctypes.c_void_p(selected), 0x00000100)
    if handle is not None:
        target = _as_int(handle, "handle")
    else:
        target = user32.GetForegroundWindow()
    if not target:
        raise RuntimeError("No foreground window is available for the layout change")
    if not user32.PostMessageW(
        wintypes.HWND(target),
        wintypes.UINT(_WM_INPUTLANGCHANGEREQUEST),
        wintypes.WPARAM(0),
        wintypes.LPARAM(selected),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "PostMessageW(WM_INPUTLANGCHANGEREQUEST) failed")
    return f"Input language change requested for window {target} (HKL {selected})."


_PRIORITY_NAMES = {
    "idle": "IDLE_PRIORITY_CLASS",
    "belownormal": "BELOW_NORMAL_PRIORITY_CLASS",
    "below_normal": "BELOW_NORMAL_PRIORITY_CLASS",
    "normal": "NORMAL_PRIORITY_CLASS",
    "abovenormal": "ABOVE_NORMAL_PRIORITY_CLASS",
    "above_normal": "ABOVE_NORMAL_PRIORITY_CLASS",
    "high": "HIGH_PRIORITY_CLASS",
    "realtime": "REALTIME_PRIORITY_CLASS",
}


def _priority_value(priority: str | int) -> tuple[int, str]:
    """Resolve a psutil Windows priority class value and display name."""
    if isinstance(priority, int):
        value = priority
    else:
        raw = str(priority).strip()
        if re.fullmatch(r"0[xX][0-9A-Fa-f]+|\d+", raw):
            value = int(raw, 0)
        else:
            key = raw.casefold().replace(" ", "").replace("-", "_")
            name = _PRIORITY_NAMES.get(key)
            if name is None:
                raise ValueError(
                    "priority must be idle, below_normal, normal, above_normal, high, realtime, "
                    "or a numeric priority class"
                )
            value = int(getattr(psutil, name))
    return value, _priority_display_name(value)


def _priority_display_name(value: int) -> str:
    """Map a numeric Windows priority class back to a readable name."""
    for name in (
        "IDLE_PRIORITY_CLASS",
        "BELOW_NORMAL_PRIORITY_CLASS",
        "NORMAL_PRIORITY_CLASS",
        "ABOVE_NORMAL_PRIORITY_CLASS",
        "HIGH_PRIORITY_CLASS",
        "REALTIME_PRIORITY_CLASS",
    ):
        if value == int(getattr(psutil, name)):
            return name
    return str(value)


def start_process(
    executable: str,
    args: str | Sequence[str] | None = None,
    cwd: str | None = None,
) -> str:
    """Start a process with separated arguments and an optional working directory."""
    if not executable or not str(executable).strip():
        raise ValueError("executable is required")
    argv = [str(executable), *_as_str_list(args)]
    working_directory = None
    if cwd:
        working_directory = str(_resolve_existing_path(cwd, directory=True))
    process = subprocess.Popen(
        argv,
        cwd=working_directory,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    return json.dumps(
        {
            "pid": process.pid,
            "executable": str(executable),
            "args": argv[1:],
            "cwd": working_directory,
        },
        ensure_ascii=False,
        indent=2,
    )


def set_process_priority(pid: int | str, priority: str | int) -> str:
    """Set a running process priority class with psutil."""
    pid_value = _as_int(pid, "pid")
    value, name = _priority_value(priority)
    process = psutil.Process(pid_value)
    previous = process.nice()
    process.nice(value)
    current = process.nice()
    return (
        f"Priority for PID {pid_value} ({process.name()}) changed from "
        f"{_priority_display_name(previous)} to {_priority_display_name(current)} ({name})."
    )


def _set_process_suspend_state(pid: int | str, suspend: bool) -> str:
    """Suspend or resume all threads in a process via NtSuspendProcess/NtResumeProcess."""
    pid_value = _as_int(pid, "pid")
    if pid_value <= 4:
        raise ValueError("System processes with PID 4 or lower cannot be suspended safely")
    if pid_value == os.getpid():
        raise ValueError("Refusing to suspend or resume the current MCP server process")
    process = psutil.Process(pid_value)
    process_name = process.name()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    function = ntdll.NtSuspendProcess if suspend else ntdll.NtResumeProcess
    function.argtypes = [wintypes.HANDLE]
    function.restype = wintypes.LONG
    handle = kernel32.OpenProcess(_PROCESS_SUSPEND_RESUME, False, pid_value)
    if not handle:
        error = ctypes.get_last_error()
        raise OSError(error, f"OpenProcess failed for PID {pid_value}")
    try:
        status = int(function(handle))
    finally:
        kernel32.CloseHandle(handle)
    if status != 0:
        action = "suspend" if suspend else "resume"
        raise RuntimeError(
            f"Nt{action.title()}Process failed for PID {pid_value}: "
            f"NTSTATUS 0x{status & 0xFFFFFFFF:08X}"
        )
    return f"Process {process_name} (PID {pid_value}) {'suspended' if suspend else 'resumed'}."


def suspend_process(pid: int | str) -> str:
    """Suspend all threads in a process.

    Suspending arbitrary processes can deadlock services or applications and
    can lead to data loss.
    """
    return _set_process_suspend_state(pid, True)


def resume_process(pid: int | str) -> str:
    """Resume all threads in a previously suspended process."""
    return _set_process_suspend_state(pid, False)


def list_process_services(
    name: str | None = None,
    status: str | None = None,
    limit: int | str = 100,
) -> str:
    """List Windows services, including status, start type, and binary path."""
    from tabulate import tabulate

    max_items = max(1, min(_as_int(limit, "limit", 100), 2000))
    needle = name.casefold() if name else None
    status_needle = status.casefold() if status else None
    rows: list[list[Any]] = []
    for service in psutil.win_service_iter():
        try:
            info = service.as_dict()
        except psutil.NoSuchProcess, psutil.AccessDenied:
            continue
        service_name = str(info.get("name") or "")
        display_name = str(info.get("display_name") or "")
        service_status = str(info.get("status") or "unknown")
        if (
            needle
            and needle not in service_name.casefold()
            and needle not in display_name.casefold()
        ):
            continue
        if status_needle and status_needle != service_status.casefold():
            continue
        rows.append(
            [
                service_name,
                display_name,
                service_status,
                info.get("start_type") or "",
                (info.get("binpath") or "")[:90],
            ]
        )
    rows.sort(key=lambda row: (str(row[0]).casefold(), str(row[1]).casefold()))
    total = len(rows)
    rows = rows[:max_items]
    if not rows:
        return "No matching Windows services found."
    suffix = f" (showing {len(rows)} of {total})" if total > len(rows) else ""
    return f"Windows services{suffix}:\n" + tabulate(
        rows,
        headers=["Name", "Display Name", "Status", "Start Type", "Binary"],
        tablefmt="simple",
    )


def control_process_service(name: str, action: Literal["start", "stop"]) -> str:
    """Start or stop a Windows service. Administrative privileges are usually required."""
    if action not in {"start", "stop"}:
        raise ValueError("action must be start or stop")
    service = psutil.win_service_get(name)
    before = service.status()
    if action == "start":
        service.start()
    else:
        service.stop()
    after = service.status()
    return f"Service {name} {action} requested; status: {before} -> {after}."


_DIALOG_SCRIPT = r"""
import json
import sys

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"tkinter unavailable: {exc}"}))
    raise SystemExit(0)

try:
    config = json.loads(sys.stdin.read() or "{}")
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"invalid config: {exc}"}))
    raise SystemExit(0)

root = tk.Tk()
root.withdraw()
try:
    mode = config.get("mode")
    title = str(config.get("title") or "Windows MCP")
    if mode == "message":
        level = str(config.get("level") or "info").lower()
        message = str(config.get("message") or "")
        detail = config.get("details")
        if detail is not None:
            message = message + "\n\n" + str(detail)
        if level == "error":
            messagebox.showerror(title, message, parent=root)
            value = "ok"
        elif level == "warning":
            messagebox.showwarning(title, message, parent=root)
            value = "ok"
        elif level == "question":
            value = "yes" if messagebox.askyesno(title, message, parent=root) else "no"
        else:
            messagebox.showinfo(title, message, parent=root)
            value = "ok"
    elif mode == "input":
        show = "*" if config.get("password") else None
        value = simpledialog.askstring(
            title,
            str(config.get("message") or "Enter a value:"),
            initialvalue=str(config.get("initial") or ""),
            show=show,
            parent=root,
        )
    elif mode == "choice":
        choices = [str(item) for item in config.get("choices") or []]
        if not choices:
            raise ValueError("choices must not be empty")
        dialog = tk.Toplevel(root)
        dialog.title(title)
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        result = {"value": None}
        ttk.Label(dialog, text=str(config.get("message") or "Choose an option:")).pack(
            padx=16, pady=(14, 6), anchor="w"
        )
        combo = ttk.Combobox(dialog, values=choices, state="readonly", width=42)
        combo.set(str(config.get("initial") or choices[0]))
        combo.pack(padx=16, pady=6)
        buttons = ttk.Frame(dialog)
        buttons.pack(padx=16, pady=(6, 14), fill="x")
        def accept_choice():
            result["value"] = combo.get()
            dialog.destroy()
        ttk.Button(buttons, text="OK", command=accept_choice).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.wait_window()
        value = result["value"]
    elif mode == "file":
        initial_dir = config.get("initial_dir") or None
        initial_file = config.get("initial_file") or None
        raw_types = config.get("filetypes") or [["All files", "*.*"]]
        filetypes = [tuple(str(part) for part in item) for item in raw_types]
        if str(config.get("file_action") or "open").lower() == "save":
            value = filedialog.asksaveasfilename(
                parent=root,
                title=title,
                initialdir=initial_dir,
                initialfile=initial_file,
                filetypes=filetypes,
            )
        elif config.get("multiple"):
            value = list(
                filedialog.askopenfilenames(
                    parent=root,
                    title=title,
                    initialdir=initial_dir,
                    filetypes=filetypes,
                )
            )
        else:
            value = filedialog.askopenfilename(
                parent=root,
                title=title,
                initialdir=initial_dir,
                filetypes=filetypes,
            )
    elif mode == "folder":
        value = filedialog.askdirectory(
            parent=root,
            title=title,
            initialdir=config.get("initial_dir") or None,
            mustexist=bool(config.get("must_exist", True)),
        )
    elif mode == "date":
        initial = str(config.get("initial") or "")
        import datetime as _datetime
        try:
            selected_date = _datetime.date.fromisoformat(initial) if initial else _datetime.date.today()
        except ValueError:
            selected_date = _datetime.date.today()
        dialog = tk.Toplevel(root)
        dialog.title(title)
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        result = {"value": None}
        ttk.Label(dialog, text=str(config.get("message") or "Choose a date:")).pack(
            padx=16, pady=(14, 6), anchor="w"
        )
        fields = ttk.Frame(dialog)
        fields.pack(padx=16, pady=6)
        year = tk.StringVar(value=str(selected_date.year))
        month = tk.StringVar(value=str(selected_date.month))
        day = tk.StringVar(value=str(selected_date.day))
        ttk.Spinbox(fields, from_=1970, to=2100, width=6, textvariable=year).grid(row=0, column=0)
        ttk.Label(fields, text="-").grid(row=0, column=1, padx=3)
        ttk.Spinbox(fields, from_=1, to=12, width=4, textvariable=month).grid(row=0, column=2)
        ttk.Label(fields, text="-").grid(row=0, column=3, padx=3)
        ttk.Spinbox(fields, from_=1, to=31, width=4, textvariable=day).grid(row=0, column=4)
        buttons = ttk.Frame(dialog)
        buttons.pack(padx=16, pady=(6, 14), fill="x")
        def accept_date():
            try:
                chosen = _datetime.date(int(year.get()), int(month.get()), int(day.get()))
            except ValueError as exc:
                messagebox.showerror(title, f"Invalid date: {exc}", parent=dialog)
                return
            except Exception as exc:
                messagebox.showerror(title, f"Invalid date: {exc}", parent=dialog)
                return
            result["value"] = chosen.isoformat()
            dialog.destroy()
        ttk.Button(buttons, text="OK", command=accept_date).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.wait_window()
        value = result["value"]
    else:
        raise ValueError(f"unsupported dialog mode: {mode}")
    if value in (None, "") or value == ():
        output = {"ok": True, "value": None, "cancelled": True}
    else:
        output = {"ok": True, "value": value, "cancelled": False}
    print(json.dumps(output, ensure_ascii=True))
except Exception as exc:
    print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
finally:
    try:
        root.destroy()
    except Exception:
        pass
"""


def _normalize_dialog_filetypes(value: Any) -> list[list[str]]:
    """Normalize filetype filters accepted from the MCP transport."""
    if value is None:
        return [["All files", "*.*"]]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("filetypes must be a JSON list of [label, pattern] pairs")
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ValueError("filetypes must be a list")
    result: list[list[str]] = []
    for item in parsed:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            result.append([str(item[0]), str(item[1])])
        else:
            raise ValueError("each filetype must be [label, pattern]")
    return result or [["All files", "*.*"]]


def show_dialog(
    mode: Literal["message", "input", "choice", "file", "folder", "date"],
    title: str = "Windows MCP",
    message: str | None = None,
    details: str | None = None,
    level: str = "info",
    initial: str | None = None,
    password: bool | str = False,
    choices: str | Sequence[str] | None = None,
    multiple: bool | str = False,
    file_action: Literal["open", "save"] = "open",
    initial_dir: str | None = None,
    initial_file: str | None = None,
    filetypes: Any = None,
    must_exist: bool | str = True,
    timeout: int | str = 300,
) -> str:
    """Show a native dialog in a separate Python/Tk subprocess.

    Running Tk in its own process avoids MCP-server UI-thread ownership issues
    and keeps the server responsive when the dialog has no parent window.
    """
    if mode not in {"message", "input", "choice", "file", "folder", "date"}:
        raise ValueError("unsupported dialog mode")
    config: dict[str, Any] = {
        "mode": mode,
        "title": title,
        "message": message,
        "details": details,
        "level": level,
        "initial": initial,
        "password": _as_bool(password),
        "choices": _as_str_list(choices),
        "multiple": _as_bool(multiple),
        "file_action": file_action,
        "initial_dir": initial_dir,
        "initial_file": initial_file,
        "filetypes": _normalize_dialog_filetypes(filetypes),
        "must_exist": _as_bool(must_exist, True),
    }
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        [sys.executable, "-c", _DIALOG_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
    )
    timeout_value = max(1, _as_int(timeout, "timeout", 300))
    try:
        stdout, stderr = process.communicate(
            json.dumps(config, ensure_ascii=True), timeout=timeout_value
        )
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TimeoutError(f"Dialog timed out after {timeout_value} seconds")
    if process.returncode != 0:
        raise RuntimeError(stderr.strip() or stdout.strip() or "Dialog subprocess failed")
    try:
        result = json.loads(stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Dialog returned invalid output: {stdout[:300]}") from exc
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Dialog failed")
    if result.get("cancelled") or result.get("value") is None:
        return "Cancelled."
    value = result.get("value")
    if isinstance(value, list):
        return "Selected:\n" + "\n".join(str(item) for item in value)
    return f"Selected: {value}"
