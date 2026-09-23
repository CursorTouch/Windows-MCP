"""SystemX tool pack — advanced Windows system operations.

This module is intentionally optional: importing it must not require packages
outside the project's declared runtime dependencies. The tool functions provide
window control, clipboard formats, archives, system controls, process/service
management, and native dialogs.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from windows_mcp import systemx
from windows_mcp.infrastructure import with_analytics


def _error(exc: Exception) -> str:
    """Return a transport-safe error string as required by this tool pack."""
    return f"Error: {type(exc).__name__}: {exc}"


def register(mcp, *, get_desktop, get_analytics):
    """Register the six SystemX capability tools on ``mcp``."""

    @mcp.tool(
        name="WindowControl",
        description=(
            "Advanced top-level window management. Keywords: window, handle, title, close, "
            "show, hide, minimize, maximize, restore, always on top, topmost, list windows. "
            "Modes: list, close, show, hide, minimize, maximize, restore, set_topmost, "
            "unset_topmost. Closing a window can discard unsaved application data."
        ),
        annotations=ToolAnnotations(
            title="WindowControl",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "WindowControl-Tool")
    def window_control_tool(
        mode: Literal[
            "list",
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
        title_contains: str | None = None,
        include_hidden: bool | str = False,
        limit: int | str = 200,
        ctx: Context = None,
    ) -> str:
        """List or control a top-level window.

        ``close`` posts WM_CLOSE and can cause an application to discard unsaved
        data. Provide ``handle``, ``title``, or ``pid`` for window actions.
        """
        try:
            if mode == "list":
                return systemx.list_windows(
                    title_contains=title_contains or title,
                    include_hidden=include_hidden,
                    limit=limit,
                )
            return systemx.control_window(
                action=mode,
                handle=handle,
                title=title,
                pid=pid,
            )
        except Exception as exc:
            return _error(exc)

    @mcp.tool(
        name="ClipboardAdvanced",
        description=(
            "Extended clipboard operations. Keywords: clipboard, copy, paste, image, files, "
            "CF_HDROP, HTML, clear clipboard, formats. Modes: get_text, set_text, get_image, "
            "set_image, get_files, set_files, get_html, set_html, clear, formats."
        ),
        annotations=ToolAnnotations(
            title="ClipboardAdvanced",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "ClipboardAdvanced-Tool")
    def clipboard_advanced_tool(
        mode: Literal[
            "get_text",
            "set_text",
            "get_image",
            "set_image",
            "get_files",
            "set_files",
            "get_html",
            "set_html",
            "clear",
            "formats",
        ],
        text: str | None = None,
        path: str | None = None,
        paths: list[str] | str | None = None,
        html: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Read or replace clipboard formats including text, images, files, and HTML.

        ``clear`` removes every format. Replacing clipboard content affects other
        applications that paste from the clipboard.
        """
        try:
            if mode == "get_text":
                return systemx.clipboard_get_text()
            if mode == "set_text":
                if text is None:
                    return "Error: text is required for set_text"
                return systemx.clipboard_set_text(text)
            if mode == "get_image":
                return systemx.clipboard_get_image(path)
            if mode == "set_image":
                if path is None:
                    return "Error: path is required for set_image"
                return systemx.clipboard_set_image(path)
            if mode == "get_files":
                return systemx.clipboard_get_files()
            if mode == "set_files":
                values = paths if paths is not None else path
                if values is None:
                    return "Error: paths is required for set_files"
                return systemx.clipboard_set_files(values)
            if mode == "get_html":
                return systemx.clipboard_get_html()
            if mode == "set_html":
                if html is None:
                    return "Error: html is required for set_html"
                return systemx.clipboard_set_html(html)
            if mode == "clear":
                return systemx.clipboard_clear()
            if mode == "formats":
                return systemx.clipboard_formats()
            return f"Error: unsupported clipboard mode: {mode}"
        except Exception as exc:
            return _error(exc)

    @mcp.tool(
        name="Archive",
        description=(
            "Create, extract, or list zip/tar/tar.gz archives. Keywords: zip, unzip, tar, "
            "tgz, compress, decompress, archive contents. Extraction rejects path traversal "
            "and links, and existing files are preserved unless overwrite=True."
        ),
        annotations=ToolAnnotations(
            title="Archive",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Archive-Tool")
    def archive_tool(
        mode: Literal["create", "extract", "list"],
        sources: list[str] | str | None = None,
        archive_path: str | None = None,
        output_dir: str | None = None,
        archive_format: Literal["auto", "zip", "tar", "tar.gz"] = "auto",
        overwrite: bool | str = False,
        ctx: Context = None,
    ) -> str:
        """Manage archive files.

        ``create`` replaces the destination archive only when overwrite=True.
        ``extract`` writes files under output_dir and can overwrite existing
        files only when overwrite=True.
        """
        try:
            if mode == "list":
                if archive_path is None:
                    return "Error: archive_path is required for list"
                return systemx.list_archive(archive_path)
            if mode == "create":
                if sources is None:
                    return "Error: sources is required for create"
                if archive_path is None:
                    return "Error: archive_path is required for create"
                return systemx.create_archive(
                    sources=sources,
                    archive_path=archive_path,
                    archive_format=archive_format,
                    overwrite=overwrite,
                )
            if mode != "extract":
                return f"Error: unsupported archive mode: {mode}"
            if output_dir is None:
                return "Error: output_dir is required for extract"
            if archive_path is None:
                return "Error: archive_path is required for extract"
            return systemx.extract_archive(
                archive_path=archive_path,
                output_dir=output_dir,
                overwrite=overwrite,
            )
        except Exception as exc:
            return _error(exc)

    @mcp.tool(
        name="SystemControl",
        description=(
            "Lock, volume, mute, brightness, input-language, screensaver and power controls. "
            "Keywords: lock workstation, volume, mute, brightness, keyboard layout, IME, "
            "screen saver, shutdown, restart, reboot, logoff, sign out, sleep, hibernate, "
            "abort shutdown. Locking immediately hides the desktop behind the secure lock "
            "screen. The power modes end the session or power the machine down and cannot be "
            "undone from inside this session: use dry_run=True to preview, and give shutdown "
            "and restart a non-zero timeout so mode='abort' can still cancel them."
        ),
        annotations=ToolAnnotations(
            title="SystemControl",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "SystemControl-Tool")
    def system_control_tool(
        mode: Literal[
            "lock",
            "volume_get",
            "volume_set",
            "mute_get",
            "mute_set",
            "brightness_get",
            "brightness_set",
            "input_language_list",
            "input_language_set",
            "screensaver",
            "shutdown",
            "restart",
            "logoff",
            "suspend",
            "hibernate",
            "abort",
        ],
        level: int | str | None = None,
        mute: bool | str | None = None,
        layout: str | int | None = None,
        handle: int | str | None = None,
        timeout: int | str = 0,
        force: bool | str = False,
        message: str | None = None,
        dry_run: bool | str = False,
        ctx: Context = None,
    ) -> str:
        """Control system-level settings.

        ``lock`` interrupts the active desktop session. ``screensaver`` asks the
        desktop to start the configured screen saver; dry_run returns the planned
        action without locking or starting it.

        The power modes end the session or power the machine down:

        * ``shutdown`` / ``restart`` honour ``timeout`` (grace seconds),
          ``force`` (close unresponsive apps) and ``message`` (shown to the
          user). A non-zero ``timeout`` keeps the door open for ``abort``.
        * ``logoff`` signs the interactive user out.
        * ``suspend`` sleeps to RAM, ``hibernate`` writes the session to disk.
        * ``abort`` cancels a pending shutdown/restart.

        Pass ``dry_run=True`` to preview any of these without acting.
        """
        try:
            if mode == "lock":
                return systemx.lock_workstation(dry_run=dry_run)
            if mode == "volume_get":
                return systemx.get_volume()
            if mode == "volume_set":
                if level is None:
                    return "Error: level is required for volume_set"
                return systemx.set_volume(level)
            if mode == "mute_get":
                return systemx.get_volume_mute()
            if mode == "mute_set":
                if mute is None:
                    return "Error: mute is required for mute_set"
                return systemx.set_volume_mute(mute)
            if mode == "brightness_get":
                return systemx.get_brightness()
            if mode == "brightness_set":
                if level is None:
                    return "Error: level is required for brightness_set"
                return systemx.set_brightness(level)
            if mode == "input_language_list":
                return systemx.list_keyboard_layouts()
            if mode == "input_language_set":
                if layout is None:
                    return "Error: layout is required for input_language_set"
                return systemx.switch_keyboard_layout(layout=layout, handle=handle)
            if mode == "screensaver":
                return systemx.start_screensaver(dry_run=dry_run)
            if mode == "shutdown":
                return systemx.shutdown(
                    timeout=timeout, force=force, message=message, dry_run=dry_run
                )
            if mode == "restart":
                return systemx.restart(
                    timeout=timeout, force=force, message=message, dry_run=dry_run
                )
            if mode == "logoff":
                return systemx.logoff(force=force, dry_run=dry_run)
            if mode == "suspend":
                return systemx.suspend(force=force, dry_run=dry_run)
            if mode == "hibernate":
                return systemx.suspend(hibernate=True, force=force, dry_run=dry_run)
            if mode == "abort":
                return systemx.abort_shutdown(dry_run=dry_run)
            return f"Error: unsupported system control mode: {mode}"
        except Exception as exc:
            return _error(exc)

    @mcp.tool(
        name="SystemProcess",
        description=(
            "Start processes and manage priority, suspension, and Windows services. "
            "Keywords: launch process, task manager, priority, suspend, resume, service, "
            "services.msc, start service, stop service. Starting or stopping services usually "
            "requires administrator rights; suspending processes can freeze applications."
        ),
        annotations=ToolAnnotations(
            title="SystemProcess",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "SystemProcess-Tool")
    def system_process_tool(
        mode: Literal[
            "start",
            "priority_set",
            "suspend",
            "resume",
            "service_list",
            "service_start",
            "service_stop",
        ],
        executable: str | None = None,
        args: list[str] | str | None = None,
        cwd: str | None = None,
        pid: int | str | None = None,
        priority: str | int | None = None,
        service: str | None = None,
        name: str | None = None,
        status: str | None = None,
        limit: int | str = 100,
        ctx: Context = None,
    ) -> str:
        """Manage processes and services.

        ``suspend`` can deadlock an application and ``service_stop`` can interrupt
        dependent workloads. Both operations should be used only with a clear need.
        """
        try:
            if mode == "start":
                if executable is None:
                    return "Error: executable is required for start"
                return systemx.start_process(executable=executable, args=args, cwd=cwd)
            if mode == "priority_set":
                if pid is None or priority is None:
                    return "Error: pid and priority are required for priority_set"
                return systemx.set_process_priority(pid=pid, priority=priority)
            if mode == "suspend":
                if pid is None:
                    return "Error: pid is required for suspend"
                return systemx.suspend_process(pid)
            if mode == "resume":
                if pid is None:
                    return "Error: pid is required for resume"
                return systemx.resume_process(pid)
            if mode == "service_list":
                return systemx.list_process_services(name=name, status=status, limit=limit)
            if mode == "service_start":
                if service is None:
                    return "Error: service is required for service_start"
                return systemx.control_process_service(name=service, action="start")
            if mode == "service_stop":
                if service is None:
                    return "Error: service is required for service_stop"
                return systemx.control_process_service(name=service, action="stop")
            return f"Error: unsupported process mode: {mode}"
        except Exception as exc:
            return _error(exc)

    @mcp.tool(
        name="SystemDialog",
        description=(
            "Show native Windows dialogs from an isolated GUI subprocess. Keywords: message box, "
            "prompt, input box, choice, dropdown, file picker, folder picker, date picker. "
            "Modes: message, input, choice, file, folder, date. Dialogs block until the user "
            "responds or the optional timeout expires."
        ),
        annotations=ToolAnnotations(
            title="SystemDialog",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "SystemDialog-Tool")
    def system_dialog_tool(
        mode: Literal["message", "input", "choice", "file", "folder", "date"],
        title: str = "Windows MCP",
        message: str | None = None,
        details: str | None = None,
        level: Literal["info", "warning", "error", "question"] = "info",
        initial: str | None = None,
        password: bool | str = False,
        choices: list[str] | str | None = None,
        multiple: bool | str = False,
        file_action: Literal["open", "save"] = "open",
        initial_dir: str | None = None,
        initial_file: str | None = None,
        filetypes: list[list[str]] | str | None = None,
        must_exist: bool | str = True,
        timeout: int | str = 300,
        ctx: Context = None,
    ) -> str:
        """Show a modal dialog in a separate process.

        The isolated Tk process avoids MCP-server UI-thread ownership issues.
        A cancelled dialog returns ``Cancelled.``; a timeout returns an Error.
        """
        try:
            return systemx.show_dialog(
                mode=mode,
                title=title,
                message=message,
                details=details,
                level=level,
                initial=initial,
                password=password,
                choices=choices,
                multiple=multiple,
                file_action=file_action,
                initial_dir=initial_dir,
                initial_file=initial_file,
                filetypes=filetypes,
                must_exist=must_exist,
                timeout=timeout,
            )
        except Exception as exc:
            return _error(exc)
