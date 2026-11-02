"""`serve` debug mode, switched on by --debug or by WINDOWS_MCP_DEBUG.

WINDOWS_MCP_DEBUG is also what the extension's "Debug Mode" setting sets
(manifest.json). Debug records must reach stderr; stdout carries the stdio
JSON-RPC protocol.
"""

import logging
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

import windows_mcp.__main__ as cli
from windows_mcp.__main__ import main

# Loggers whose level serve() changes in debug mode ("" is the root logger).
_DEBUG_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "fastmcp")


def _prepare_serve(monkeypatch):
    monkeypatch.setattr(
        cli.asyncio, "WindowsSelectorEventLoopPolicy", lambda: object(), raising=False
    )
    monkeypatch.setattr(cli.asyncio, "set_event_loop_policy", lambda _policy: None)
    monkeypatch.setattr(cli, "discover_config_path", lambda _path: None)
    monkeypatch.setattr(cli, "_run_server", lambda **_kwargs: None)


@contextmanager
def _unconfigured_logging():
    """Give serve() the root logger of a fresh process, then undo its changes.

    pytest attaches its capture handlers to the root logger, and while the root
    logger has any handler, logging.basicConfig() does nothing.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_levels = {name: logging.getLogger(name).level for name in _DEBUG_LOGGERS}
    for handler in saved_handlers:
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)
    try:
        yield root
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)


def _serve(monkeypatch, args=(), debug_env=None):
    """Invoke serve with WINDOWS_MCP_DEBUG set to debug_env (None leaves it unset).

    Returns the result and the root logger's level and handlers as serve left them.
    """
    _prepare_serve(monkeypatch)
    with _unconfigured_logging() as root:
        result = CliRunner().invoke(main, ["serve", *args], env={"WINDOWS_MCP_DEBUG": debug_env})
        return result, root.level, root.handlers[:]


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
def test_debug_env_var_logs_debug_records_to_stderr(monkeypatch, value):
    result, level, _handlers = _serve(monkeypatch, debug_env=value)

    assert result.exit_code == 0, result.output
    assert level == logging.DEBUG
    assert "Starting windows-mcp" in result.stderr
    assert result.stdout == ""


def test_debug_flag_logs_debug_records_to_stderr(monkeypatch):
    result, level, _handlers = _serve(monkeypatch, ["--debug"])

    assert result.exit_code == 0, result.output
    assert level == logging.DEBUG
    assert "Starting windows-mcp" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("value", [None, "false", "0", ""], ids=["unset", "false", "0", "empty"])
def test_logging_is_left_alone_without_debug_mode(monkeypatch, value):
    result, level, handlers = _serve(monkeypatch, debug_env=value)

    assert result.exit_code == 0, result.output
    assert level == logging.WARNING
    assert handlers == []
    assert "Starting windows-mcp" not in result.stderr
