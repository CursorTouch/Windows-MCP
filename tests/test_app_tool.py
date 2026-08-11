import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from windows_mcp.tools import app


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


class FakeDesktop:
    def __init__(self) -> None:
        self.app_calls: list[tuple[object, ...]] = []

    def app(
        self,
        mode: str,
        name: str | None,
        window_loc: list[int] | None,
        window_size: list[int] | None,
    ) -> str:
        self.app_calls.append((mode, name, window_loc, window_size))
        return "legacy app result"


def _mcp(desktop: FakeDesktop | None = None) -> FakeMCP:
    mcp = FakeMCP()
    resolved_desktop = desktop or FakeDesktop()
    app.register(
        mcp,
        get_desktop=lambda: resolved_desktop,
        get_analytics=lambda: None,
    )
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

    monkeypatch.setattr(app.subprocess, "Popen", fake_popen)
    mcp = _mcp()

    result = json.loads(
        asyncio.run(
            mcp.tools["App"](
                mode="launch_executable",
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
            "stdin": app.subprocess.DEVNULL,
            "stdout": app.subprocess.DEVNULL,
            "stderr": app.subprocess.DEVNULL,
            "close_fds": True,
        }
    ]

    annotations = mcp.tool_options["App"]["annotations"]
    assert annotations.destructiveHint is True
    assert annotations.idempotentHint is False


def test_launch_executable_accepts_json_string_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        app.subprocess,
        "Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=1),
    )

    result = json.loads(
        asyncio.run(
            _mcp().tools["App"](
                mode="launch_executable",
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
        app.subprocess,
        "Popen",
        lambda *popen_args, **kwargs: pytest.fail("Popen should not be called"),
    )

    with pytest.raises(ValueError, match="args must be a list of strings"):
        asyncio.run(
            _mcp().tools["App"](
                mode="launch_executable",
                executable=str(exe),
                args=args,
            )
        )


def test_launch_executable_requires_executable() -> None:
    with pytest.raises(
        ValueError,
        match='executable is required for mode="launch_executable"',
    ):
        asyncio.run(_mcp().tools["App"](mode="launch_executable"))


def test_launch_executable_rejects_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Executable does not exist"):
        asyncio.run(
            _mcp().tools["App"](
                mode="launch_executable",
                executable=str(tmp_path / "missing.exe"),
            )
        )


def test_launch_executable_rejects_missing_cwd(tmp_path: Path) -> None:
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Working directory does not exist"):
        asyncio.run(
            _mcp().tools["App"](
                mode="launch_executable",
                executable=str(exe),
                cwd=str(tmp_path / "missing"),
            )
        )


@pytest.mark.parametrize(
    ("mode", "kwargs"),
    [
        ("launch", {"executable": "ignored.exe"}),
        ("resize", {"args": []}),
        ("switch", {"cwd": "."}),
    ],
)
def test_exact_launch_inputs_require_launch_executable_mode(
    mode: str,
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(
        ValueError,
        match='executable, args, and cwd require mode="launch_executable"',
    ):
        asyncio.run(_mcp().tools["App"](mode=mode, **kwargs))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "app"},
        {"window_loc": [10, 20]},
        {"window_size": [800, 600]},
    ],
)
def test_launch_executable_rejects_legacy_mode_inputs(
    kwargs: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe = tmp_path / "app.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        app.subprocess,
        "Popen",
        lambda *popen_args, **popen_kwargs: pytest.fail("Popen should not be called"),
    )

    with pytest.raises(ValueError, match="not supported"):
        asyncio.run(
            _mcp().tools["App"](
                mode="launch_executable",
                executable=str(exe),
                **kwargs,
            )
        )


@pytest.mark.parametrize(
    ("mode", "name", "window_loc", "window_size"),
    [
        ("launch", "Notepad", None, None),
        ("resize", "Notepad", [10, 20], [800, 600]),
        ("switch", "Notepad", None, None),
    ],
)
def test_existing_app_modes_delegate_unchanged(
    mode: str,
    name: str,
    window_loc: list[int] | None,
    window_size: list[int] | None,
) -> None:
    desktop = FakeDesktop()

    result = asyncio.run(
        _mcp(desktop).tools["App"](
            mode=mode,
            name=name,
            window_loc=window_loc,
            window_size=window_size,
        )
    )

    assert result == "legacy app result"
    assert desktop.app_calls == [(mode, name, window_loc, window_size)]
