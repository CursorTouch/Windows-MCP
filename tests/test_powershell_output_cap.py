"""Regression tests for issue #583 — PowerShell tool output handling.

Two problems with what the tool returned:

1. Output was returned whole however long it was. MCP clients cap tool-result
   size and pass none of it to the model when the cap is exceeded, so a
   command that printed ~750 KB returned nothing usable, not even the exit
   status. The cap now keeps the head and the tail (the tail holds the errors
   and the summary) with a marker in between.
2. A timeout was reported as ``Status Code: 1``, the same as a command that
   ran and failed, with the partial output discarded. Timeouts now report a
   status no real process can exit with, the elapsed time, and whatever the
   command printed before ending.
"""

import asyncio
import subprocess
from collections.abc import Callable

from windows_mcp.powershell import COMMAND_TIMED_OUT
from windows_mcp.powershell import service as powershell_service
from windows_mcp.powershell.service import PowerShellExecutor
from windows_mcp.tools import shell as shell_module
from windows_mcp.tools.shell import cap_output


# ---------------------------------------------------------------------------
# cap_output
# ---------------------------------------------------------------------------


def test_cap_output_passthrough_under_limit():
    assert cap_output("short output", 1000) == "short output"


def test_cap_output_disabled_for_zero_and_negative():
    text = "x" * 5000
    assert cap_output(text, 0) == text
    assert cap_output(text, -1) == text


def test_cap_output_keeps_head_and_tail_and_reports_the_omitted_span():
    text = "HEAD" + "x" * 40_000 + "TAIL"
    capped = cap_output(text, 1000)
    assert capped.startswith("HEAD")
    assert capped.endswith("TAIL")
    assert "characters omitted" in capped
    # The marker must point at the way to the full output.
    assert "FileSystem" in capped
    assert "offset/limit" in capped
    assert len(capped) < 1000 + 250


def test_cap_output_keeps_more_of_the_tail_than_the_head():
    # Distinct halves so the exact split is observable.
    text = "h" * 5000 + "t" * 5000
    capped = cap_output(text, 1000)
    head_length = len(capped) - len(capped.lstrip("h"))
    tail_length = len(capped) - len(capped.rstrip("t"))
    assert head_length > 0
    assert tail_length > head_length


def test_cap_output_uses_short_marker_when_limit_is_small():
    text = "x" * 100
    capped = cap_output(text, 50)
    assert len(capped) <= 50
    assert capped.startswith("x")
    assert capped.endswith("x")


def test_cap_output_never_exceeds_requested_limit():
    text = "HEAD" + "x" * 40_000 + "TAIL"
    for limit in (1, 10, 31, 32, 50, 220, 1000):
        assert len(cap_output(text, limit)) <= limit


# ---------------------------------------------------------------------------
# execute_command timeout reporting
# ---------------------------------------------------------------------------


def _raise_timeout(output=None, stderr=None):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(["pwsh", "-Command"], 5, output=output, stderr=stderr)

    return fake_run


def test_execute_command_reports_timeout_with_a_non_exit_status(monkeypatch):
    monkeypatch.setattr(
        powershell_service,
        "run_with_graceful_timeout",
        _raise_timeout(output=b"progress line\n", stderr=b"boom\n"),
    )
    response, status = PowerShellExecutor.execute_command("Start-Sleep 100", timeout=5)

    assert status == COMMAND_TIMED_OUT
    assert status == -1
    assert "timed out" in response
    assert "(timeout limit: 5s)" in response
    assert "progress line" in response
    assert "boom" in response


def test_execute_command_reports_timeout_without_output(monkeypatch):
    monkeypatch.setattr(powershell_service, "run_with_graceful_timeout", _raise_timeout())
    response, status = PowerShellExecutor.execute_command("Start-Sleep 100", timeout=5)

    assert status == COMMAND_TIMED_OUT
    assert "No output was collected before the timeout" in response


def test_execute_command_failure_status_is_unchanged(monkeypatch):
    monkeypatch.setattr(
        powershell_service,
        "run_with_graceful_timeout",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, b"err out", b""),
    )
    response, status = PowerShellExecutor.execute_command("whoami", timeout=5)

    assert status == 1
    assert response == "err out"


# ---------------------------------------------------------------------------
# tool-level wiring
# ---------------------------------------------------------------------------


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


def _shell_tool() -> Callable:
    mcp = FakeMCP()
    shell_module.register(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    return mcp.tools["PowerShell"]


class _StubExecutor:
    def __init__(self, response: str, status_code: int) -> None:
        self.response = response
        self.status_code = status_code
        self.calls: list[tuple[str, int]] = []

    def execute_command(self, command: str, timeout: int) -> tuple[str, int]:
        self.calls.append((command, timeout))
        return self.response, self.status_code


def test_tool_caps_large_output_and_keeps_the_status(monkeypatch):
    stub = _StubExecutor("HEAD" + "x" * 50_000 + "TAIL", 0)
    monkeypatch.setattr(shell_module, "PowerShellExecutor", stub)
    tool = _shell_tool()

    result = asyncio.run(tool("Get-ChildItem -Recurse", timeout=10, max_output=1000))

    assert stub.calls == [("Get-ChildItem -Recurse", 10)]
    assert result.startswith("Response: HEAD")
    assert result.endswith("TAIL\nStatus Code: 0")
    assert "characters omitted" in result
    assert result.rstrip().endswith("Status Code: 0")


def test_tool_reports_timeouts_as_timed_out(monkeypatch):
    stub = _StubExecutor("Command execution timed out after 12.3s (timeout limit: 5s)", -1)
    monkeypatch.setattr(shell_module, "PowerShellExecutor", stub)
    tool = _shell_tool()

    result = asyncio.run(tool("Start-Sleep 100", timeout=5))

    assert "Status Code: timed out" in result
    assert "Status Code: -1" not in result
    # The partial-output message from the executor survives capping untouched.
    assert "Command execution timed out after 12.3s" in result


def test_tool_passes_a_default_max_output_well_under_client_limits(monkeypatch):
    stub = _StubExecutor("ok", 0)
    monkeypatch.setattr(shell_module, "PowerShellExecutor", stub)
    tool = _shell_tool()

    asyncio.run(tool("Get-Date"))

    assert stub.calls == [("Get-Date", 30)]
