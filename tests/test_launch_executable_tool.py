import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from windows_mcp.tools import launch


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}
        self.tool_options: dict[str, dict[str, object]] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        self.tool_options[name] = kwargs

        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


def _mcp() -> FakeMCP:
    mcp = FakeMCP()
    launch.register(mcp, get_desktop=lambda: None, get_analytics=lambda: None)
    return mcp


def test_launch_executable_preserves_argv_and_uses_no_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")
    cwd = tmp_path / "work dir"
    cwd.mkdir()
    popen_calls: list[dict[str, object]] = []

    def fake_popen(command: list[str], **kwargs: object) -> SimpleNamespace:
        popen_calls.append({"command": command, **kwargs})
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(launch.subprocess, "Popen", fake_popen)
    mcp = _mcp()

    result = json.loads(
        asyncio.run(
            mcp.tools["LaunchExecutable"](
                executable=str(exe),
                args=["--name", "value with spaces", "", "-dash"],
                cwd=str(cwd),
            )
        )
    )

    assert result == {
        "pid": 1234,
        "executable": str(exe.resolve()),
        "args": ["--name", "value with spaces", "", "-dash"],
        "cwd": str(cwd.resolve()),
    }
    assert popen_calls == [
        {
            "command": [
                str(exe.resolve()),
                "--name",
                "value with spaces",
                "",
                "-dash",
            ],
            "cwd": str(cwd.resolve()),
            "shell": False,
            "stdin": launch.subprocess.DEVNULL,
            "stdout": launch.subprocess.DEVNULL,
            "stderr": launch.subprocess.DEVNULL,
            "close_fds": True,
        }
    ]

    annotations = mcp.tool_options["LaunchExecutable"]["annotations"]
    assert annotations.destructiveHint is True
    assert annotations.idempotentHint is False


def test_launch_executable_accepts_json_string_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        launch.subprocess,
        "Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=1),
    )

    result = json.loads(
        asyncio.run(
            _mcp().tools["LaunchExecutable"](
                executable=str(exe),
                args='["--flag", "hello"]',
            )
        )
    )

    assert result["args"] == ["--flag", "hello"]


@pytest.mark.parametrize(
    "args",
    [
        ["valid", 1],
        '{"not": "a list"}',
    ],
)
def test_launch_executable_rejects_non_string_args(
    args: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        launch.subprocess,
        "Popen",
        lambda *popen_args, **kwargs: pytest.fail("Popen should not be called"),
    )

    with pytest.raises(ValueError, match="args must be a list of strings"):
        asyncio.run(_mcp().tools["LaunchExecutable"](executable=str(exe), args=args))


def test_launch_executable_rejects_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Executable does not exist"):
        asyncio.run(_mcp().tools["LaunchExecutable"](executable=str(tmp_path / "missing.exe")))


def test_launch_executable_rejects_missing_cwd(tmp_path: Path) -> None:
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Working directory does not exist"):
        asyncio.run(
            _mcp().tools["LaunchExecutable"](
                executable=str(exe),
                cwd=str(tmp_path / "missing"),
            )
        )
