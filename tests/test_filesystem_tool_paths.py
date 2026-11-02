"""FileSystem must read a leading ~ as the user's home folder.

Relative paths are resolved from the Desktop, and os.path.isabs() is false for
"~/notes.txt", so FileSystem(mode="write", path="~/notes.txt") created a folder
named "~" on the Desktop and wrote the file there. As in PowerShell, only "~"
alone or followed by a slash or a backslash is the home folder: names such as
"~$report.docx" (an Office lock file) stay relative to the Desktop.
"""

import asyncio
from collections.abc import Callable
from unittest.mock import MagicMock

import platformdirs
import pytest

from windows_mcp.tools.filesystem import register


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


@pytest.fixture
def file_system() -> Callable[..., str]:
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: MagicMock(), get_analytics=lambda: None)
    tool = mcp.tools["FileSystem"]
    return lambda **kwargs: asyncio.run(tool(**kwargs))


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(platformdirs, "user_desktop_dir", lambda: str(desktop))
    return desktop


@pytest.fixture
def home(tmp_path, monkeypatch):
    # Named after the user like a real profile folder, so that a plain
    # os.path.expanduser() would expand "~name" into a sibling folder.
    home = tmp_path / "Users" / "tester"
    home.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("USERNAME", "tester")
    return home


@pytest.mark.parametrize("path", ["~/Documents/report.md", "~\\Documents\\report.md"])
def test_write_to_a_tilde_path_lands_in_home(file_system, desktop, home, path):
    file_system(mode="write", path=path, content="quarterly")

    assert (home / "Documents" / "report.md").read_text(encoding="utf-8") == "quarterly"
    assert not (desktop / "~").exists()


def test_read_and_list_of_tilde_paths_use_home(file_system, desktop, home):
    (home / "notes.txt").write_text("in home", encoding="utf-8")

    assert file_system(mode="read", path="~/notes.txt").endswith("\nin home")
    assert "[FILE] notes.txt" in file_system(mode="list", path="~")


def test_copy_to_a_tilde_destination_lands_in_home(file_system, desktop, home):
    (desktop / "report.txt").write_text("quarterly", encoding="utf-8")

    file_system(mode="copy", path="report.txt", destination="~/report.txt")

    assert (home / "report.txt").read_text(encoding="utf-8") == "quarterly"
    assert not (desktop / "~").exists()


@pytest.mark.parametrize("name", ["~$report.docx", "~WRL0001.tmp"])
def test_names_starting_with_a_tilde_stay_relative_to_the_desktop(file_system, desktop, home, name):
    (desktop / name).write_text("lock", encoding="utf-8")

    assert file_system(mode="read", path=name).endswith("\nlock")


def test_relative_paths_still_resolve_from_the_desktop(file_system, desktop, home):
    file_system(mode="write", path="notes.txt", content="on the desktop")

    assert (desktop / "notes.txt").read_text(encoding="utf-8") == "on the desktop"
