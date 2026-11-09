"""Recursive list and search must not walk into directory junctions.

Path.rglob does not follow symlinks while expanding its recursive part, but it
does enter junctions. A junction that points at one of its own ancestors (two
packages that depend on each other in a pnpm workspace link each other this
way) was walked again and again until the path grew too long, and every file
under it was listed once per round.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from windows_mcp.filesystem import service

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")


def _junction(link: Path, target: Path) -> None:
    # mklink /J needs no elevation, unlike a symlink.
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Two packages that link each other through node_modules junctions."""
    for name in ("a", "b"):
        package = tmp_path / "packages" / name
        (package / "node_modules").mkdir(parents=True)
        (package / "index.js").write_text(name)
    _junction(tmp_path / "packages/a/node_modules/b", tmp_path / "packages/b")
    _junction(tmp_path / "packages/b/node_modules/a", tmp_path / "packages/a")
    return tmp_path


def _paths(listing: str) -> list[str]:
    """The relative paths in a list or search answer ("  [DIR ] a\\b")."""
    return sorted(
        match.group(1)
        for line in listing.splitlines()[1:]
        if (match := re.match(r"\s*\[(?:DIR |FILE)\]\s+(\S+)", line))
    )


def test_search_finds_each_file_once(workspace: Path) -> None:
    result = service.search_files(str(workspace), "index.js")

    assert result.splitlines()[0].endswith("(2 matches)"), result
    assert _paths(result) == [
        r"packages\a\index.js",
        r"packages\b\index.js",
    ]


def test_recursive_list_shows_junctions_without_entering_them(workspace: Path) -> None:
    result = service.list_directory(str(workspace), recursive=True)

    assert _paths(result) == [
        "packages",
        r"packages\a",
        r"packages\a\index.js",
        r"packages\a\node_modules",
        r"packages\a\node_modules\b",
        r"packages\b",
        r"packages\b\index.js",
        r"packages\b\node_modules",
        r"packages\b\node_modules\a",
    ]


def test_recursive_list_with_pattern_matches_like_rglob(tmp_path: Path) -> None:
    # Without junctions, the walk matches the same entries rglob did.
    for relative in ("x.txt", "sub/y.txt", "sub/deeper/z.TXT", "sub/notes.md"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("")

    for pattern in ("*.txt", "sub/*.txt", "**/*.md", "*"):
        walked = sorted(service._walk(tmp_path, pattern))
        assert walked == sorted(tmp_path.rglob(pattern)), pattern


def test_search_with_double_star_is_not_recursive_into_junctions(workspace: Path) -> None:
    # recursive=False still lets ** recurse; it must not loop either.
    result = service.search_files(str(workspace), "**/index.js", recursive=False)

    assert result.splitlines()[0].endswith("(2 matches)"), result


def test_volume_mounted_in_a_folder_is_entered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A volume mount point is a junction to \\?\Volume{...}\, not to a folder
    # that can lead back to an ancestor. (Mounting a volume needs elevation, so
    # the junction's target is faked.)
    target = tmp_path / "target"
    target.mkdir()
    _junction(tmp_path / "mounted", target)
    real_readlink = service.os.readlink

    def readlink(path: str) -> str:
        if path.endswith("mounted"):
            return "\\\\?\\Volume{d19e4094-adb9-491c-b1ba-4b35ef640edb}\\"
        return real_readlink(path)

    monkeypatch.setattr(service.os, "readlink", readlink)
    entries = {entry.name: entry for entry in service.os.scandir(tmp_path)}

    assert service._enters(entries["mounted"])
    assert service._enters(entries["target"])
