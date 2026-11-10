"""PowerShell command executor service"""

import base64
import ctypes
import ctypes.wintypes
import logging
import os
import winreg
import shutil
import subprocess
from collections.abc import Callable
from time import perf_counter

from windows_mcp.desktop.utils import is_elevated
from windows_mcp.powershell.utils import decode_clixml, run_with_graceful_timeout

__all__ = ["COMMAND_TIMED_OUT", "PowerShellExecutor"]

logger = logging.getLogger(__name__)

#: Status returned when the tool's timeout ended the command. Windows exit
#: codes are unsigned, so no real process can exit with this value, letting
#: callers tell a timeout apart from an ordinary failure (#583).
COMMAND_TIMED_OUT = -1


def _timeout_partial_output(exc: subprocess.TimeoutExpired) -> str:
    """Decode whatever the timed-out command printed before it was ended.

    ``run_with_graceful_timeout`` refills ``exc.stdout``/``exc.stderr`` with the
    output gathered during its graceful-shutdown attempts; the plain timeout
    path used to drop it.
    """
    chunks: list[str] = []
    for stream in (exc.stdout, exc.stderr):
        if not stream:
            continue
        if isinstance(stream, bytes):
            stream = stream.decode("utf-8", errors="replace")
        chunk = decode_clixml(stream).strip()
        if chunk:
            chunks.append(chunk)
    return "\n".join(chunks)


def _read_reg_env(hkey: int, subkey: str) -> tuple[dict[str, str], str, str]:
    """Read all environment variables from a registry key.

    Returns (vars, path, pathext) where *vars* is a dict of name->value
    (excluding PATH/PATHEXT), and *path*/*pathext* are the raw expanded
    values for those two special keys (empty string if absent).
    """
    variables: dict[str, str] = {}
    path = ""
    pathext = ""

    try:
        with winreg.OpenKey(hkey, subkey) as key:
            i = 0
            while True:
                try:
                    name, value, reg_type = winreg.EnumValue(key, i)
                    if reg_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                        i += 1
                        continue
                    if reg_type == winreg.REG_EXPAND_SZ:
                        # Note: ExpandEnvironmentStrings uses the current process
                        # environment, which may be stripped by the MCP host. References
                        # to missing vars (e.g. %COMPUTERNAME%) won't expand correctly.
                        # In practice this is acceptable: most REG_EXPAND_SZ values under
                        # HKLM/HKCU\Environment reference %SystemRoot% or %USERPROFILE%
                        # which are almost always present.
                        value = winreg.ExpandEnvironmentStrings(value)
                    upper = name.upper()
                    if upper == "PATH":
                        path = value
                    elif upper == "PATHEXT":
                        pathext = value
                    else:
                        variables[name] = value
                    i += 1
                except OSError:
                    break
    except OSError:
        pass

    return variables, path, pathext


def _dedup_path(*segments: str) -> str:
    """Join PATH segments and deduplicate entries (case-insensitive)."""
    seen = set()
    deduped = []
    for p in ";".join(filter(None, segments)).split(";"):
        norm = p.lower().rstrip("\\")
        if norm and norm not in seen:
            seen.add(norm)
            deduped.append(p)
    return ";".join(deduped)


def _win32_name(dll: str, func: str) -> str:
    """Call a Win32 GetXxxNameW(LPWSTR, LPDWORD) function.

    Only suitable for APIs with the (buffer, &size) calling convention,
    e.g. kernel32.GetComputerNameW and advapi32.GetUserNameW.
    """
    buf = ctypes.create_unicode_buffer(257)
    size = ctypes.wintypes.DWORD(257)
    fn = getattr(ctypes.windll, dll)
    if getattr(fn, func)(buf, ctypes.byref(size)):
        return buf.value
    return ""


_FALLBACK_PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL;.PY;.PYW"


def _prepare_env() -> dict[str, str]:
    """Prepare a complete environment block for the PowerShell subprocess.

    MCP hosts (e.g. Claude Desktop) may launch this server with a stripped
    environment block missing session-level variables. This function starts
    from os.environ and fills in missing variables from:
      1. System-level env vars from the registry (HKLM)
      2. User-level env vars from the registry (HKCU)
      3. Dynamic vars (COMPUTERNAME, USERNAME, etc.) via Win32 API
    Existing values in os.environ are never overwritten, only missing ones
    are supplemented. PATH is special-cased: inherited entries keep their
    priority and registry entries are appended to fill in missing paths.
    """
    env = os.environ.copy()

    # PYTHONHOME is set by uv to pin its own managed interpreter for this
    # server process. Leaking it into a spawned shell makes any Python found
    # on PATH there resolve its standard library against the server's
    # interpreter instead of its own, breaking unrelated installs (#350).
    env.pop("PYTHONHOME", None)

    try:
        machine_vars, machine_path, machine_pathext = _read_reg_env(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        )
        user_vars, user_path, user_pathext = _read_reg_env(winreg.HKEY_CURRENT_USER, r"Environment")

        # Supplement missing vars. User-level (HKCU) takes precedence over
        # system-level (HKLM) for same-named keys, matching Windows' resolution order.
        # Names are matched without case, as Windows does. os.environ.copy()
        # upper-cases them ("ONEDRIVE"), while the registry keeps the case they
        # were created with ("OneDrive", "PSModulePath"). A second key that
        # differs only in case also reaches the child, and there the later key
        # wins, so the registry value would replace the inherited one.
        names = {name.upper(): name for name in env}
        registry_vars = {
            name.upper(): (name, value)
            for name, value in (*machine_vars.items(), *user_vars.items())
        }
        for upper, (name, value) in registry_vars.items():
            name = names.get(upper, name)
            if not env.get(name):
                env[name] = value

        # PATH: inherited entries keep their priority; registry entries are
        # appended to fill in anything missing (e.g. stripped MCP host env).
        if machine_path or user_path:
            env["PATH"] = _dedup_path(env.get("PATH", ""), machine_path, user_path)

        # PATHEXT: use registry value if the inherited one looks incomplete
        effective_pathext = user_pathext or machine_pathext
        if effective_pathext and ".EXE" not in env.get("PATHEXT", "").upper():
            env["PATHEXT"] = effective_pathext

    except Exception:
        logger.debug("Failed to read environment from registry", exc_info=True)
        if ".EXE" not in env.get("PATHEXT", "").upper():
            env["PATHEXT"] = _FALLBACK_PATHEXT

    # Dynamic variables not stored in registry — only fill if missing
    if not env.get("COMPUTERNAME"):
        try:
            computer_name = _win32_name("kernel32", "GetComputerNameW")
            if computer_name:
                env["COMPUTERNAME"] = computer_name
        except Exception as e:
            logger.debug("Failed to get COMPUTERNAME via Win32 API: %s", e)

    if not env.get("USERNAME"):
        try:
            user_name = _win32_name("advapi32", "GetUserNameW")
            if user_name:
                env["USERNAME"] = user_name
        except Exception as e:
            logger.debug("Failed to get USERNAME via Win32 API: %s", e)

    user_profile = os.path.expanduser("~")
    env.setdefault("USERPROFILE", user_profile)
    drive, tail = os.path.splitdrive(user_profile)
    env.setdefault("HOMEDRIVE", drive)
    env.setdefault("HOMEPATH", tail)
    # On domain-joined machines USERDOMAIN is the NetBIOS domain name (e.g.
    # "CORP"), not the computer name. This fallback is only correct for workgroup
    # machines. USERDOMAIN is a session variable set at logon and is unlikely to
    # be missing on domain-joined hosts, so this is an acceptable last resort.
    env.setdefault("USERDOMAIN", env.get("COMPUTERNAME", ""))

    return env


_WINDOWS_POWERSHELL = os.path.join("System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def _resolve_shell(shell: str | None, env: dict[str, str]) -> str:
    """Resolve the PowerShell executable to launch against the child's PATH.

    Windows looks a bare program name up on the *server's* PATH, not on the
    environment handed to the child, so the PATH repaired by ``_prepare_env()``
    does not decide which executable is found. ``System32\\WindowsPowerShell\\v1.0``
    is not on the default search list either, so when the MCP host strips the
    server's PATH a bare ``powershell`` fails with WinError 2 (#411).

    Args:
        shell: The shell the caller asked for, or ``None`` to prefer ``pwsh`` and
            fall back to Windows PowerShell.
        env: The environment the child will run with.

    Returns:
        An absolute path when the shell can be found; otherwise Windows
        PowerShell's well-known location for a PowerShell request, or the name
        unchanged for any other shell so the launch error names it.
    """
    search_path = env.get("PATH")
    for name in (shell,) if shell else ("pwsh", "powershell"):
        found = shutil.which(name, path=search_path)
        if found:
            return found

    if shell and os.path.basename(shell).lower().removesuffix(".exe") != "powershell":
        return shell
    system_root = env.get("SystemRoot") or os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(system_root, _WINDOWS_POWERSHELL)


class PowerShellExecutor:
    """Static utility class for executing PowerShell commands."""

    @staticmethod
    def execute_command(
        command: str,
        timeout: int = 10,
        shell: str | None = None,
        *,
        on_output: Callable[[str, bytes], None] | None = None,
    ) -> tuple[str, int]:
        """Run *command* in PowerShell and return its output and exit status.

        Args:
            command: The PowerShell command or script to run.
            timeout: Seconds before the command is ended.
            shell: The shell to launch, or ``None`` to prefer ``pwsh``.
            on_output: Optional callback that sees the raw output as it arrives
                (``run_with_graceful_timeout``'s ``on_output``), for reporting
                progress while the command runs. It does not change the result.

        Returns:
            The output and the exit status, or ``COMMAND_TIMED_OUT`` when the
            timeout ended the command.
        """
        try:
            # $OutputEncoding: controls how PS5.1 encodes output written to its stdout pipe.
            # Without this set to UTF-8, PS5.1 uses the system codepage and native process
            # stdout is silently lost when Python reads the pipe.
            # [Console]::OutputEncoding: controls how PS decodes bytes from native exe stdout.
            utf8_command = (
                "$OutputEncoding = [System.Text.Encoding]::UTF8; "
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                f"{command}"
            )
            encoded = base64.b64encode(utf8_command.encode("utf-16le")).decode("ascii")
            env = _prepare_env()
            # NO_COLOR suppresses ANSI escape sequences in pwsh 7.2+ (and many other CLI tools).
            # PS5.1 has no ANSI output, so this is harmlessly ignored there.
            # https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_ansi_terminals#disabling-ansi-output
            env["NO_COLOR"] = "1"

            shell = _resolve_shell(shell, env)

            args = [shell, "-NoProfile"]
            # Only older Windows PowerShell (5.1) uses -OutputFormat Text successfully here
            shell_name = os.path.basename(shell).lower().replace(".exe", "")
            if shell_name == "powershell":
                args.extend(["-OutputFormat", "Text"])
            args.extend(["-EncodedCommand", encoded])

            started_at = perf_counter()
            result = run_with_graceful_timeout(
                args,
                stdin=subprocess.DEVNULL,  # Prevent child processes from inheriting the MCP pipe stdin
                capture_output=True,  # No errors='ignore' - let subprocess return bytes
                timeout=timeout,
                cwd=os.path.expanduser(path="~"),
                env=env,
                on_output=on_output,
            )
            # Handle both bytes and str output (subprocess behavior varies by environment)
            stdout = result.stdout
            stderr = result.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            stderr = decode_clixml(stderr or "")
            output = stdout or stderr
            # If the command failed with "Access is denied" and we aren't elevated, add a helpful hint
            if result.returncode != 0 and "Access is denied" in output and not is_elevated():
                output += (
                    "\n\nHINT: This command may require an elevated (Administrator) terminal. "
                    "The Windows-MCP server is currently running at a lower integrity level."
                )
            return output, result.returncode
        except subprocess.TimeoutExpired as exc:
            # Report the timeout under a status no real process can exit with
            # (#583): the command may have done part of its work, and retrying
            # with a longer timeout is the usual fix — a different reaction
            # than for a command that ran and failed. Include how long it ran
            # and whatever it printed before ending.
            elapsed = perf_counter() - started_at
            partial = _timeout_partial_output(exc)
            message = (
                f"Command execution timed out after {elapsed:.1f}s (timeout limit: {timeout}s)"
            )
            if partial:
                message += f". Output collected before the timeout:\n{partial}"
            else:
                message += ". No output was collected before the timeout."
            return message, COMMAND_TIMED_OUT
        except Exception as e:
            # WinError 2 does not say which file was missing, so name the shell.
            logger.warning("PowerShell command failed to run (shell=%s)", shell, exc_info=True)
            return f"Command execution failed: {type(e).__name__}: {e} (shell: {shell})", 1
