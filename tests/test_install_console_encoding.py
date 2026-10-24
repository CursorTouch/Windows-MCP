"""`windows-mcp install` must read schtasks and PowerShell output in the console code page.

On a zh-CN system those tools write GBK. Decoding that output as UTF-8 (or as the ANSI
code page) raised UnicodeDecodeError in subprocess's reader thread, which printed a
traceback during an install that otherwise succeeded.
"""

import subprocess
import sys

import windows_mcp.__main__ as cli

# "成功" (success) in GBK, followed by a byte that is not valid UTF-8 in any position.
_GBK_OUTPUT = b"\xb3\xc9\xb9\xa6\xff ok"


def test_console_tool_output_is_decoded_with_the_console_code_page():
    script = f"import sys; sys.stdout.buffer.write({_GBK_OUTPUT!r})"
    result = cli._run_console_tool([sys.executable, "-c", script])

    assert result.returncode == 0
    assert result.stdout == _GBK_OUTPUT.decode("oem", errors="replace")


def test_schtasks_and_powershell_use_the_console_code_page(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args[0], kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    cli._schtasks("/Query", "/TN", "windows-mcp-server")
    cli._register_task_powershell("windows-mcp-server", r"C:\start-server.cmd")

    assert [program for program, _ in calls] == ["schtasks", "powershell"]
    for _, kwargs in calls:
        assert kwargs["encoding"] == "oem"
        assert kwargs["errors"] == "replace"
