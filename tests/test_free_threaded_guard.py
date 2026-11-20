"""The server refuses to start on a free-threaded CPython.

Neither `.python-version` nor `[tool.uv] python-preference` can keep uv from
resolving a `3.14+freethreaded` build, and the COM/UIAutomation stack is not
supported there, so startup fails with a clear message instead.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import windows_mcp
from windows_mcp.interpreter import exit_if_free_threaded

# Runs `python -m windows_mcp serve` (the extension's command) with the GIL
# probe reporting a free-threaded build, and reports whether fastmcp had been
# imported by the time startup exited.
_FREE_THREADED_SERVE = """
import runpy, sys
sys._is_gil_enabled = lambda: False
sys.argv = ["windows-mcp", "serve"]
try:
    runpy.run_module("windows_mcp", run_name="__main__", alter_sys=True)
finally:
    print("fastmcp imported:", "fastmcp" in sys.modules)
"""


def test_serve_exits_before_importing_the_server_on_a_free_threaded_build():
    env = dict(os.environ)
    src = str(Path(windows_mcp.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [src, env.get("PYTHONPATH")]))

    result = subprocess.run(
        [sys.executable, "-c", _FREE_THREADED_SERVE],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert result.returncode == 1, result.stderr
    assert "free-threaded" in result.stderr
    assert "fastmcp imported: False" in result.stdout


def test_refuses_to_start_when_the_gil_is_disabled(monkeypatch, capsys):
    monkeypatch.setattr(sys, "_is_gil_enabled", lambda: False, raising=False)

    with pytest.raises(SystemExit) as excinfo:
        exit_if_free_threaded()

    assert excinfo.value.code not in (0, None)
    err = capsys.readouterr().err
    assert "free-threaded" in err
    assert "uv python install 3.14" in err


def test_starts_when_the_gil_is_enabled(monkeypatch, capsys):
    monkeypatch.setattr(sys, "_is_gil_enabled", lambda: True, raising=False)

    exit_if_free_threaded()

    assert capsys.readouterr().err == ""


def test_starts_on_an_interpreter_without_the_gil_probe(monkeypatch, capsys):
    monkeypatch.delattr(sys, "_is_gil_enabled", raising=False)

    exit_if_free_threaded()

    assert capsys.readouterr().err == ""
