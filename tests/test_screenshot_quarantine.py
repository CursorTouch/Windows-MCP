from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

import windows_mcp.desktop.screenshot as screenshot
from windows_mcp.__main__ import _configure_transport_runtime


@pytest.fixture(autouse=True)
def reset_circuit(monkeypatch):
    monkeypatch.setattr(screenshot, "_isolation_failures", 0)
    monkeypatch.setattr(screenshot, "_circuit_open_until", 0.0)


def test_stdio_enables_safe_screenshot_defaults(monkeypatch):
    keys = [
        "WINDOWS_MCP_DISABLE_FLASH",
        "WINDOWS_MCP_SCREENSHOT_BACKEND",
        "WINDOWS_MCP_SCREENSHOT_ISOLATION",
        "WINDOWS_MCP_SCREENSHOT_QUARANTINED",
        "WINDOWS_MCP_SCREENSHOT_TIMEOUT_SECONDS",
        "WINDOWS_MCP_SCREENSHOT_FAILURE_THRESHOLD",
        "WINDOWS_MCP_SCREENSHOT_COOLDOWN_SECONDS",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)

    _configure_transport_runtime("stdio")

    assert screenshot.os.environ["WINDOWS_MCP_DISABLE_FLASH"] == "1"
    assert screenshot.os.environ["WINDOWS_MCP_SCREENSHOT_BACKEND"] == "mss"
    assert screenshot.os.environ["WINDOWS_MCP_SCREENSHOT_ISOLATION"] == "1"
    assert screenshot.os.environ["WINDOWS_MCP_SCREENSHOT_QUARANTINED"] == "1"
    assert screenshot.os.environ["WINDOWS_MCP_SCREENSHOT_TIMEOUT_SECONDS"] == "15"
    assert screenshot.os.environ["WINDOWS_MCP_SCREENSHOT_FAILURE_THRESHOLD"] == "2"
    assert screenshot.os.environ["WINDOWS_MCP_SCREENSHOT_COOLDOWN_SECONDS"] == "120"


def test_isolated_capture_returns_child_image(monkeypatch):
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_ISOLATION", "1")
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_WORKER", "0")

    def fake_run(command, **kwargs):
        output = Path(command[command.index("--output") + 1])
        result = Path(command[command.index("--result") + 1])
        Image.new("RGB", (3, 2), "white").save(output, format="PNG")
        result.write_text('{"status":"ok","backend":"mss"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(screenshot.subprocess, "run", fake_run)

    image, backend = screenshot.capture(None, backend="mss")

    assert image.size == (3, 2)
    assert backend == "mss"
    assert screenshot._isolation_failures == 0
    assert screenshot._circuit_open_until == 0.0


def test_timeout_opens_circuit_and_blocks_second_child(monkeypatch):
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_ISOLATION", "1")
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_WORKER", "0")
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_COOLDOWN_SECONDS", "60")
    calls = 0

    def timeout_run(command, **kwargs):
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired(command, timeout=kwargs["timeout"])

    monkeypatch.setattr(screenshot.subprocess, "run", timeout_run)

    with pytest.raises(RuntimeError, match="timed out"):
        screenshot.capture(None, backend="mss")
    with pytest.raises(RuntimeError, match="circuit breaker is open"):
        screenshot.capture(None, backend="mss")

    assert calls == 1


def test_native_exit_code_is_reported_as_hex(monkeypatch):
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_ISOLATION", "1")
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_WORKER", "0")

    def native_crash(command, **kwargs):
        return subprocess.CompletedProcess(command, -1073741819, stdout="", stderr="native crash")

    monkeypatch.setattr(screenshot.subprocess, "run", native_crash)

    with pytest.raises(RuntimeError, match="0xC0000005"):
        screenshot.capture(None, backend="mss")

def test_quarantined_capture_fails_before_starting_child(monkeypatch):
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_QUARANTINED", "1")
    monkeypatch.setenv("WINDOWS_MCP_SCREENSHOT_ISOLATION", "1")
    called = False

    def should_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("child must not start while quarantined")

    monkeypatch.setattr(screenshot.subprocess, "run", should_not_run)
    with pytest.raises(RuntimeError, match="quarantined"):
        screenshot.capture(None, backend="mss")
    assert called is False
