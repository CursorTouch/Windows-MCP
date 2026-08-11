"""FileSystem tool — file and directory operations."""

import os
from typing import Literal

from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics
from windows_mcp import filesystem
from fastmcp import Context


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name='FileSystem',
        description="Manages file system operations with eight modes: 'read' (read text file contents with optional line offset/limit), 'write' (create or overwrite a file, set append=True to append), 'copy' (copy file or directory to destination), 'move' (move or rename file/directory), 'delete' (delete file or directory, set recursive=True for non-empty dirs), 'list' (list directory contents with optional pattern filter), 'search' (find files matching a glob pattern), 'info' (get file/directory metadata like size, dates, type). Relative paths are resolved from the user's Desktop folder. Use absolute paths to access other locations.",
        annotations=ToolAnnotations(
            title="FileSystem",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "FileSystem-Tool")
    def file_system_tool(
        mode: Literal['read', 'write', 'copy', 'move', 'delete', 'list', 'search', 'info'],
        path: str,
        destination: str | None = None,
        content: str | None = None,
        pattern: str | None = None,
        recursive: bool | str = False,
        append: bool | str = False,
        overwrite: bool | str = False,
        offset: int | None = None,
        limit: int | None = None,
        encoding: str = 'utf-8',
        show_hidden: bool | str = False,
        ctx: Context = None,
    ) -> str:
        try:
            from platformdirs import user_desktop_dir
            default_dir = user_desktop_dir()
            if not os.path.isabs(path):
                path = os.path.join(default_dir, path)
            if destination and not os.path.isabs(destination):
                destination = os.path.join(default_dir, destination)

            recursive = recursive is True or (isinstance(recursive, str) and recursive.lower() == 'true')
            append = append is True or (isinstance(append, str) and append.lower() == 'true')
            overwrite = overwrite is True or (isinstance(overwrite, str) and overwrite.lower() == 'true')
            show_hidden = show_hidden is True or (isinstance(show_hidden, str) and show_hidden.lower() == 'true')

            match mode:
                case 'read':
                    return filesystem.read_file(path, offset=offset, limit=limit, encoding=encoding)
                case 'write':
                    if content is None:
                        return 'Error: content parameter is required for write mode.'
                    return filesystem.write_file(path, content, append=append, encoding=encoding)
                case 'copy':
                    if destination is None:
                        return 'Error: destination parameter is required for copy mode.'
                    return filesystem.copy_path(path, destination, overwrite=overwrite)
                case 'move':
                    if destination is None:
                        return 'Error: destination parameter is required for move mode.'
                    return filesystem.move_path(path, destination, overwrite=overwrite)
                case 'delete':
                    return filesystem.delete_path(path, recursive=recursive)
                case 'list':
                    return filesystem.list_directory(path, pattern=pattern, recursive=recursive, show_hidden=show_hidden)
                case 'search':
                    if pattern is None:
                        return 'Error: pattern parameter is required for search mode.'
                    return filesystem.search_files(path, pattern, recursive=recursive)
                case 'info':
                    return filesystem.get_file_info(path)
                case _:
                    return f'Error: Unknown mode "{mode}". Use: read, write, copy, move, delete, list, search, info.'
        except Exception as e:
            raise
