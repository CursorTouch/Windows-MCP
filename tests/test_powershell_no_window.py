"""Regression tests for issue #369 — shell calls flashing a console window.

Without CREATE_NO_WINDOW, a console child started from a server that has no
console of its own gets a brand new console allocated for it: a window flashes
and steals keyboard focus on every tool call. Redirecting the streams does not
prevent the allocation, so the flag has to be set explicitly — on the child
itself and on the taskkill fallbacks used by the timeout path.
"""

import subprocess
import sys

import pytest

from windows_mcp.powershell.utils import run_with_graceful_timeout

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only creation flags")


class TestCreationFlags:
    def test_child_gets_no_window_and_new_process_group(self):
        result = run_with_graceful_timeout(
            ["cmd", "/c", "echo hello"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "hello"

    def test_flags_are_applied_to_popen(self, monkeypatch):
        captured = {}
        real_popen = subprocess.Popen

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return real_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", spy)
        run_with_graceful_timeout(["cmd", "/c", "echo hi"], capture_output=True, text=True)

        flags = captured["creationflags"]
        assert flags & subprocess.CREATE_NO_WINDOW, "console window would flash and steal focus"
        assert flags & subprocess.CREATE_NEW_PROCESS_GROUP, "CTRL_BREAK_EVENT would hit the server"

    def test_caller_supplied_flags_are_preserved(self, monkeypatch):
        captured = {}
        real_popen = subprocess.Popen

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return real_popen(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", spy)
        run_with_graceful_timeout(
            ["cmd", "/c", "echo hi"],
            capture_output=True,
            text=True,
            creationflags=subprocess.IDLE_PRIORITY_CLASS,
        )

        flags = captured["creationflags"]
        assert flags & subprocess.IDLE_PRIORITY_CLASS
        assert flags & subprocess.CREATE_NO_WINDOW
        assert flags & subprocess.CREATE_NEW_PROCESS_GROUP

    def test_taskkill_fallback_is_windowless(self, monkeypatch):
        """The timeout path must not flash a window either."""
        calls = []
        real_run = subprocess.run

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return real_run(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)

        # Sleep well past both the timeout and the grace period, and ignore
        # CTRL_BREAK, so the graceful stop fails and taskkill is reached.
        with pytest.raises(subprocess.TimeoutExpired):
            run_with_graceful_timeout(
                ["cmd", "/c", "ping 127.0.0.1 -n 30 > nul"],
                capture_output=True,
                timeout=0.5,
                grace_period=0.5,
            )

        taskkills = [kw for args, kw in calls if args and args[0][0] == "taskkill"]
        assert taskkills, "expected the force-kill fallback to run"
        for kwargs in taskkills:
            assert kwargs.get("creationflags", 0) & subprocess.CREATE_NO_WINDOW


class TestBehaviorUnchanged:
    """The flag must not disturb capture, exit codes, or the graceful-stop path."""

    def test_exit_code_is_propagated(self):
        result = run_with_graceful_timeout(["cmd", "/c", "exit 3"], capture_output=True)
        assert result.returncode == 3

    def test_stderr_is_captured(self):
        result = run_with_graceful_timeout(
            ["cmd", "/c", "echo oops 1>&2"], capture_output=True, text=True
        )
        assert "oops" in result.stderr

    def test_stdin_input_still_works(self):
        result = run_with_graceful_timeout(
            ["cmd", "/c", "findstr x"], input="axb\nzzz\n", capture_output=True, text=True
        )
        assert "axb" in result.stdout

    def test_check_raises_on_failure(self):
        with pytest.raises(subprocess.CalledProcessError):
            run_with_graceful_timeout(["cmd", "/c", "exit 1"], capture_output=True, check=True)

    def test_timeout_still_raises(self):
        with pytest.raises(subprocess.TimeoutExpired):
            run_with_graceful_timeout(
                ["cmd", "/c", "ping 127.0.0.1 -n 30 > nul"],
                capture_output=True,
                timeout=0.5,
                grace_period=0.5,
            )
