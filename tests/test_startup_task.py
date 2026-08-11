from pathlib import Path
from unittest.mock import Mock

from click.testing import CliRunner
import pytest

from windows_mcp.__main__ import main
import windows_mcp.__main__ as cli


def test_resolve_program_uses_running_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys, "executable", "C:\\Tools\\python.exe")

    assert cli._resolve_program() == ["C:\\Tools\\python.exe", "-m", "windows_mcp"]


def test_install_writes_start_script_and_creates_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    start_script = tmp_path / "start server.cmd"
    monkeypatch.setattr(cli, "_START_SCRIPT_PATH", start_script)
    monkeypatch.setattr(cli.sys, "executable", "C:\\Program Files\\Python\\python.exe")

    calls: list[tuple[str, ...]] = []
    registrations: list[tuple[str, str]] = []

    def fake_schtasks(*args: str) -> Mock:
        calls.append(args)
        if args[:2] == ("/Query", "/TN"):
            return Mock(
                returncode=1,
                stdout="",
                stderr="ERROR: The system cannot find the file specified.",
            )
        return Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "_schtasks", fake_schtasks)
    monkeypatch.setattr(
        cli,
        "_register_task_powershell",
        lambda task_name, script_path: registrations.append((task_name, script_path))
        or Mock(returncode=0, stdout="", stderr=""),
    )

    result = runner.invoke(
        main,
        ["install", "--transport", "sse", "--host", "127.0.0.1", "--port", "9000"],
    )

    assert result.exit_code == 0, result.output
    script = start_script.read_text(encoding="utf-8")
    assert (
        '"C:\\Program Files\\Python\\python.exe" -m windows_mcp serve --transport sse '
        "--host 127.0.0.1 --port 9000"
    ) in script
    assert '1>>"' in script
    assert '2>>"' in script
    assert registrations == [(cli._TASK_NAME, str(start_script))]
    assert ("/Run", "/TN", cli._TASK_NAME) in calls


def test_uninstall_removes_task_and_wrapper(monkeypatch, tmp_path):
    runner = CliRunner()
    start_script = tmp_path / "start-server.cmd"
    start_script.write_text("@echo off\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_START_SCRIPT_PATH", start_script)

    calls = []

    def fake_schtasks(*args: str):
        calls.append(args)
        if args[:2] == ("/End", "/TN"):
            return Mock(returncode=0, stdout="", stderr="")
        return Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "_schtasks", fake_schtasks)

    result = runner.invoke(main, ["uninstall"])

    assert result.exit_code == 0, result.output
    assert not start_script.exists()
    assert ("/End", "/TN", cli._TASK_NAME) in calls
    assert ("/Delete", "/TN", cli._TASK_NAME, "/F") in calls
