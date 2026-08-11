import subprocess

import pytest
from click.testing import CliRunner

from windows_mcp import __main__ as wm


@pytest.mark.parametrize(
    ("args", "suggestion"),
    [
        (["--transport", "sse"], "windows-mcp serve --transport sse"),
        (["--debug"], "windows-mcp serve --debug"),
        (["--port", "8000"], "windows-mcp serve --port 8000"),
    ],
)
def test_legacy_serve_flags_at_group_level_get_migration_hint(args, suggestion):
    result = CliRunner().invoke(wm.main, args)

    assert result.exit_code == 2
    assert "`windows-mcp` is now a command group" in result.output
    assert suggestion in result.output
    assert "windows-mcp serve --help" in result.output


def test_serve_options_still_work_on_serve_subcommand_help():
    result = CliRunner().invoke(wm.main, ["serve", "--transport", "stdio", "--help"])

    assert result.exit_code == 0
    assert "--transport" in result.output


def test_install_options_are_not_blocked_by_legacy_filter(monkeypatch, tmp_path):
    def fake_schtasks(*args: str) -> subprocess.CompletedProcess:
        returncode = 1 if args[:2] == ("/Query", "/TN") else 0
        return subprocess.CompletedProcess(["schtasks", *args], returncode, "", "")

    monkeypatch.setattr(wm, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wm, "_START_SCRIPT_PATH", tmp_path / "start-server.cmd")
    monkeypatch.setattr(wm, "_resolve_program", lambda: ["windows-mcp"])
    monkeypatch.setattr(wm, "_schtasks", fake_schtasks)

    result = CliRunner().invoke(wm.main, ["install", "--transport", "sse"])

    assert result.exit_code == 0, result.output
    assert "Scheduled task installed" in result.output
    assert "windows-mcp serve --transport sse" in (tmp_path / "start-server.cmd").read_text(
        encoding="utf-8"
    )


def test_group_help_still_renders():
    result = CliRunner().invoke(wm.main, ["--help"])

    assert result.exit_code == 0
    assert "Commands:" in result.output


def test_bare_group_still_reports_missing_command():
    result = CliRunner().invoke(wm.main, [])

    assert result.exit_code == 2
    assert "Missing command" in result.output
