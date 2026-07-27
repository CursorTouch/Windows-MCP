"""
File system service for the Windows MCP server.
Provides structured, safe file operations as an alternative to raw Shell commands.
"""

from datetime import datetime
from pathlib import Path
import fnmatch
import logging
import shutil
import os
from windows_mcp.desktop.utils import is_elevated

from windows_mcp.filesystem.views import (
    MAX_READ_SIZE,
    MAX_RESULTS,
    File,
    Directory,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def read_file(path: str, offset: int | None = None, limit: int | None = None, encoding: str = 'utf-8') -> str:
    """Read the contents of a text file."""
    file_path = Path(path).resolve()

    if not file_path.exists():
        return f'Error: File not found: {file_path}'
    if not file_path.is_file():
        return f'Error: Path is not a file: {file_path}'
    if file_path.stat().st_size > MAX_READ_SIZE:
        return f'Error: File too large ({file_path.stat().st_size:,} bytes). Maximum is {MAX_READ_SIZE:,} bytes. Use offset/limit parameters or the Shell tool for large files.'

    try:
        with open(file_path, 'r', encoding=encoding, errors='replace') as f:
            if offset is not None or limit is not None:
                lines = f.readlines()
                start = (offset or 1) - 1  # Convert 1-based to 0-based
                start = max(0, start)
                end = start + limit if limit else len(lines)
                selected = lines[start:end]
                total = len(lines)
                content = ''.join(selected)
                return f'File: {file_path}\nLines {start + 1}-{min(end, total)} of {total}:\n{content}'
            else:
                content = f.read()
                return f'File: {file_path}\n{content}'
    except UnicodeDecodeError:
        return f'Error: Unable to read file as text with encoding "{encoding}". File may be binary.'
    except PermissionError:
        msg = f'Error: Permission denied: {file_path}'
        if not is_elevated():
            msg += "\n\nHINT: This operation may require an elevated (Administrator) terminal."
        return msg
    except Exception as e:
        return f'Error reading file: {e}'


def write_file(path: str, content: str, append: bool = False, encoding: str = 'utf-8', create_parents: bool = True) -> str:
    """Write or append text content to a file."""
    file_path = Path(path).resolve()

    try:
        if create_parents:
            file_path.parent.mkdir(parents=True, exist_ok=True)

        mode = 'a' if append else 'w'
        with open(file_path, mode, encoding=encoding) as f:
            f.write(content)

        action = 'Appended to' if append else 'Written to'
        size = file_path.stat().st_size
        return f'{action} {file_path} ({size:,} bytes)'
    except PermissionError:
        msg = f'Error: Permission denied: {file_path}'
        if not is_elevated():
            msg += "\n\nHINT: This operation may require an elevated (Administrator) terminal."
        return msg
    except Exception as e:
        return f'Error writing file: {e}'


def copy_path(source: str, destination: str, overwrite: bool = False) -> str:
    """Copy a file or directory to a new location."""
    src = Path(source).resolve()
    dst = Path(destination).resolve()

    if not src.exists():
        return f'Error: Source not found: {src}'

    if dst.exists() and not overwrite:
        return f'Error: Destination already exists: {dst}. Set overwrite=True to replace.'

    try:
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            return f'Copied file: {src} -> {dst}'
        elif src.is_dir():
            if dst.exists() and overwrite:
                shutil.rmtree(str(dst))
            shutil.copytree(str(src), str(dst))
            return f'Copied directory: {src} -> {dst}'
        else:
            return f'Error: Unsupported file type: {src}'
    except PermissionError:
        msg = 'Error: Permission denied.'
        if not is_elevated():
            msg += "\n\nHINT: This operation may require an elevated (Administrator) terminal."
        return msg
    except Exception as e:
        return f'Error copying: {e}'


def move_path(source: str, destination: str, overwrite: bool = False) -> str:
    """Move or rename a file or directory."""
    src = Path(source).resolve()
    dst = Path(destination).resolve()

    if not src.exists():
        return f'Error: Source not found: {src}'

    if dst.exists() and not overwrite:
        return f'Error: Destination already exists: {dst}. Set overwrite=True to replace.'

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and overwrite:
            if dst.is_dir():
                shutil.rmtree(str(dst))
            else:
                dst.unlink()
        shutil.move(str(src), str(dst))
        return f'Moved: {src} -> {dst}'
    except PermissionError:
        msg = 'Error: Permission denied.'
        if not is_elevated():
            msg += "\n\nHINT: This operation may require an elevated (Administrator) terminal."
        return msg
    except Exception as e:
        return f'Error moving: {e}'


def delete_path(path: str, recursive: bool = False) -> str:
    """Delete a file or directory."""
    target = Path(path).resolve()

    if not target.exists():
        return f'Error: Path not found: {target}'

    try:
        if target.is_file() or target.is_symlink():
            target.unlink()
            return f'Deleted file: {target}'
        elif target.is_dir():
            if not recursive:
                # Check if directory is empty
                if any(target.iterdir()):
                    return f'Error: Directory is not empty: {target}. Set recursive=True to delete non-empty directories.'
                target.rmdir()
            else:
                shutil.rmtree(str(target))
            return f'Deleted directory: {target}'
        else:
            return f'Error: Unsupported file type: {target}'
    except PermissionError:
        msg = f'Error: Permission denied: {target}'
        if not is_elevated():
            msg += "\n\nHINT: This operation may require an elevated (Administrator) terminal."
        return msg
    except Exception as e:
        return f'Error deleting: {e}'


def list_directory(path: str, pattern: str | None = None, recursive: bool = False, show_hidden: bool = False) -> str:
    """List contents of a directory."""
    dir_path = Path(path).resolve()

    if not dir_path.exists():
        return f'Error: Directory not found: {dir_path}'
    if not dir_path.is_dir():
        return f'Error: Path is not a directory: {dir_path}'

    try:
        entries: list[str] = []
        count = 0

        if recursive:
            iterator = dir_path.rglob(pattern or '*')
        else:
            iterator = dir_path.iterdir()

        for entry in sorted(iterator, key=lambda e: (not e.is_dir(), e.name.lower())):
            if not show_hidden and entry.name.startswith('.'):
                continue
            if pattern and not recursive and not fnmatch.fnmatch(entry.name, pattern):
                continue

            count += 1
            if count > MAX_RESULTS:
                entries.append(f'... (truncated, {MAX_RESULTS}+ items)')
                break

            try:
                size = entry.stat().st_size if entry.is_file() else 0
            except OSError:
                size = 0

            rel = str(entry.relative_to(dir_path)) if recursive else entry.name
            dir_entry = Directory(name=entry.name, is_dir=entry.is_dir(), size=size)
            entries.append(dir_entry.to_string(relative_path=rel))

        if not entries:
            filter_msg = f' matching "{pattern}"' if pattern else ''
            return f'Directory {dir_path} is empty{filter_msg}.'

        header = f'Directory: {dir_path}'
        if pattern:
            header += f' (filter: {pattern})'
        return f'{header}\n' + '\n'.join(entries)
    except PermissionError:
        msg = f'Error: Permission denied: {dir_path}'
        if not is_elevated():
            msg += "\n\nHINT: This operation may require an elevated (Administrator) terminal."
        return msg
    except Exception as e:
        return f'Error listing directory: {e}'


def search_files(path: str, pattern: str, recursive: bool = True) -> str:
    """Search for files matching a glob pattern."""
    search_root = Path(path).resolve()

    if not search_root.exists():
        return f'Error: Search path not found: {search_root}'
    if not search_root.is_dir():
        return f'Error: Search path is not a directory: {search_root}'

    try:
        results: list[str] = []
        count = 0

        if recursive:
            iterator = search_root.rglob(pattern)
        else:
            iterator = search_root.glob(pattern)

        for match in sorted(iterator, key=lambda e: e.name.lower()):
            count += 1
            if count > MAX_RESULTS:
                results.append(f'... (truncated, {MAX_RESULTS}+ matches)')
                break

            try:
                size = match.stat().st_size if match.is_file() else 0
            except OSError:
                size = 0

            rel = str(match.relative_to(search_root))
            dir_entry = Directory(name=match.name, is_dir=match.is_dir(), size=size)
            results.append(dir_entry.to_string(relative_path=rel))

        if not results:
            return f'No matches found for "{pattern}" in {search_root}'

        return f'Search: "{pattern}" in {search_root} ({min(count, MAX_RESULTS)} matches)\n' + '\n'.join(results)
    except PermissionError:
        msg = f'Error: Permission denied: {search_root}'
        if not is_elevated():
            msg += "\n\nHINT: This operation may require an elevated (Administrator) terminal."
        return msg
    except Exception as e:
        return f'Error searching: {e}'


def get_file_info(path: str) -> str:
    """Get detailed metadata about a file or directory."""
    target = Path(path).resolve()

    if not target.exists():
        return f'Error: Path not found: {target}'

    try:
        stat = target.stat()
        file_type = 'Directory' if target.is_dir() else 'File' if target.is_file() else 'Symlink' if target.is_symlink() else 'Other'

        file = File(
            path=str(target),
            type=file_type,
            size=stat.st_size,
            created=datetime.fromtimestamp(stat.st_ctime),
            modified=datetime.fromtimestamp(stat.st_mtime),
            accessed=datetime.fromtimestamp(stat.st_atime),
            read_only=not os.access(target, os.W_OK),
        )

        if target.is_dir():
            try:
                items = list(target.iterdir())
                file.contents_dirs = sum(1 for i in items if i.is_dir())
                file.contents_files = sum(1 for i in items if i.is_file())
            except PermissionError:
                pass

        if target.is_file():
            file.extension = target.suffix or '(none)'

        if target.is_symlink():
            file.link_target = str(os.readlink(target))

        return file.to_string()
    except PermissionError:
        msg = f'Error: Permission denied: {target}'
        if not is_elevated():
            msg += "\n\nHINT: This operation may require an elevated (Administrator) terminal."
        return msg
    except Exception as e:
        return f'Error getting file info: {e}'
