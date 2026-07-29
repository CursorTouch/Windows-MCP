import pytest

from windows_mcp import __main__ as wm


def test_missing_dependency_exits_nonzero_with_actionable_stderr(capsys):
    exc = ModuleNotFoundError("No module named 'win32com.shell'", name="win32com.shell")

    with pytest.raises(SystemExit) as excinfo:
        wm._exit_missing_dependency(exc)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "win32com.shell" in captured.err
    assert "uv sync" in captured.err
    # stdout carries the MCP stdio protocol stream and must stay clean.
    assert captured.out == ""
