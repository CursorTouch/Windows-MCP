"""Regression tests for issue #411 — the PowerShell tool failing with WinError 2.

MCP hosts can start the server with a stripped PATH. ``_prepare_env()`` rebuilds
PATH from the registry for the child, but Windows looks a bare program name up
on the *server's* PATH, not on the environment handed to the child, and
``System32\\WindowsPowerShell\\v1.0`` is not on the default search list. So every
call failed with ``FileNotFoundError: [WinError 2]`` although the child's
environment was complete — and the error never said which file was missing.
"""

import os
import subprocess
import sys

import pytest

from windows_mcp.powershell import service
from windows_mcp.powershell.service import PowerShellExecutor

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PATH resolution")

SYSTEM_ROOT = os.environ.get("SystemRoot", r"C:\Windows")


@pytest.fixture
def stripped_server_path(monkeypatch):
    """The server's own PATH as a stripping MCP host leaves it: no PowerShell dirs."""
    monkeypatch.setenv("PATH", f"{SYSTEM_ROOT}\\system32;{SYSTEM_ROOT}")


@pytest.fixture
def popen_spy(monkeypatch):
    launched = []
    real_popen = subprocess.Popen

    def spy(args, *rest, **kwargs):
        launched.append(args)
        return real_popen(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    return launched


class TestStrippedServerPath:
    def test_command_runs(self, stripped_server_path):
        output, code = PowerShellExecutor.execute_command("Write-Output ok")
        assert (output.strip(), code) == ("ok", 0)

    def test_explicitly_named_shell_runs(self, stripped_server_path):
        # The notification service asks for Windows PowerShell by name.
        output, code = PowerShellExecutor.execute_command("Write-Output ok", shell="powershell")
        assert (output.strip(), code) == ("ok", 0)

    def test_launches_an_absolute_existing_executable(self, stripped_server_path, popen_spy):
        PowerShellExecutor.execute_command("Write-Output ok")
        executable = popen_spy[0][0]
        assert os.path.isabs(executable), executable
        assert os.path.isfile(executable), executable


class TestResolveShell:
    def test_prefers_pwsh_on_the_child_path(self, tmp_path):
        (tmp_path / "pwsh.exe").write_bytes(b"")
        resolved = service._resolve_shell(None, {"PATH": str(tmp_path)})
        assert os.path.normcase(resolved) == os.path.normcase(str(tmp_path / "pwsh.exe"))

    def test_falls_back_to_windows_powershell(self, tmp_path):
        env = {"PATH": str(tmp_path), "SystemRoot": SYSTEM_ROOT}
        resolved = service._resolve_shell(None, env)
        expected = os.path.join(
            SYSTEM_ROOT, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
        )
        assert os.path.normcase(resolved) == os.path.normcase(expected)

    def test_unknown_explicit_shell_is_left_as_named(self, tmp_path):
        assert service._resolve_shell("pwsh-preview", {"PATH": str(tmp_path)}) == "pwsh-preview"


def test_failure_names_the_executable_and_is_logged(monkeypatch, caplog):
    missing = r"C:\nonexistent\pwsh.exe"
    monkeypatch.setattr(service, "_resolve_shell", lambda shell, env: missing)

    with caplog.at_level("WARNING", logger=service.__name__):
        output, code = PowerShellExecutor.execute_command("Write-Output ok")

    assert code == 1
    assert "FileNotFoundError" in output
    assert missing in output
    assert any(missing in record.getMessage() for record in caplog.records)
