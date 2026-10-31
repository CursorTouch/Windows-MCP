"""The start script written by `windows-mcp install` must run from a non-ASCII profile folder.

The script is UTF-8, but cmd.exe decodes a batch file with the console code page. When the
profile folder name had non-ASCII characters, the interpreter and log paths came out garbled:
cmd printed "The system cannot find the path specified.", the server never started and
neither log file was created.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import windows_mcp.__main__ as cli

# "Jose" with an acute e and a Chinese name, built with chr() to keep this file ASCII.
PROFILE_NAMES = ["Jos" + chr(0xE9), chr(0x5F20) + chr(0x4E09)]


@pytest.mark.parametrize("profile", PROFILE_NAMES, ids=["latin", "chinese"])
def test_start_script_runs_from_non_ascii_profile_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, profile: str
) -> None:
    home = tmp_path / profile
    config_dir = home / ".windows-mcp"
    start_script = config_dir / "start-server.cmd"
    # Stands in for the server: prints the arguments it was started with.
    fake_server = home / "fake_server.py"
    home.mkdir()
    fake_server.write_text("import sys\nprint(' '.join(sys.argv[1:]))\n", encoding="ascii")

    def fake_schtasks(*args: str) -> subprocess.CompletedProcess:
        returncode = 1 if args[:2] == ("/Query", "/TN") else 0
        return subprocess.CompletedProcess(["schtasks", *args], returncode, "", "")

    monkeypatch.setattr(cli, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(cli, "_START_SCRIPT_PATH", start_script)
    monkeypatch.setattr(cli, "_resolve_program", lambda: [sys.executable, str(fake_server)])
    monkeypatch.setattr(cli, "_schtasks", fake_schtasks)
    # Never register a real task in this test.
    monkeypatch.setattr(
        cli,
        "_register_task_powershell",
        lambda *args: subprocess.CompletedProcess([], 0, "", ""),
    )

    result = CliRunner().invoke(
        cli.main, ["install", "--transport", "sse", "--host", "127.0.0.1", "--port", "9000"]
    )
    assert result.exit_code == 0, result.output
    # The script below must only ever start the stand-in, never a real server.
    assert str(fake_server) in start_script.read_text(encoding="utf-8")

    # Run it the way the scheduled task does, with `cmd.exe /c` in a console of its own.
    run = subprocess.run(
        ["cmd.exe", "/d", "/c", str(start_script)],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=60,
    )

    assert run.returncode == 0, run.stderr.decode("oem", errors="replace")
    log = (config_dir / "server.log").read_text(encoding="utf-8")
    assert log.strip() == "serve --transport sse --host 127.0.0.1 --port 9000"
