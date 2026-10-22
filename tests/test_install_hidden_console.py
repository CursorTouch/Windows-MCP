"""`windows-mcp install` must not leave a visible console window open at logon.

A scheduled task whose action executes a .cmd directly gets a visible console
(a Windows Terminal window on Windows 11) for the lifetime of the server. The task
action must go through ``conhost.exe --headless`` instead.
"""

import subprocess

import windows_mcp.__main__ as cli


def test_task_action_uses_headless_conhost(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    script = r"C:\Users\me\.windows-mcp\start-server.cmd"
    cli._register_task_powershell("Windows-MCP", script)

    ps = captured["args"][-1]
    assert "-Execute 'conhost.exe'" in ps
    assert f'--headless cmd.exe /c "{script}"' in ps
    assert f"-Execute '{script}'" not in ps, "a directly executed .cmd shows a console window"


def test_headless_argument_quotes_paths_with_spaces():
    arg = cli._headless_task_argument(r"C:\Users\John Doe\.windows-mcp\start-server.cmd")
    assert arg == r'--headless cmd.exe /c "C:\Users\John Doe\.windows-mcp\start-server.cmd"'
