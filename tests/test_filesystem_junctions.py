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


@pytest.mark.parametrize("operation", [service.copy_path, service.move_path])
def test_overwrite_junction_replaces_link_without_touching_target(
    tmp_path: Path, operation
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("preserve me", encoding="utf-8")
    link = tmp_path / "link"
    _junction(link, target)

    source = tmp_path / "source"
    source.mkdir()
    (source / "new.txt").write_text("replacement", encoding="utf-8")

    result = operation(str(source), str(link), overwrite=True)

    assert not result.startswith("Error:"), result
    assert not link.is_junction()
    assert [p.name for p in link.iterdir()] == ["new.txt"]
    assert (target / "keep.txt").read_text(encoding="utf-8") == "preserve me"
    assert [p.name for p in target.iterdir()] == ["keep.txt"]
    assert source.exists() is (operation is service.copy_path)


def test_move_junction_moves_link_without_moving_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("stay put", encoding="utf-8")
    link = tmp_path / "link"
    _junction(link, target)
    destination = tmp_path / "moved_link"

    result = service.move_path(str(link), str(destination))

    assert not result.startswith("Error:"), result
    assert not link.exists() and not link.is_junction()
    assert destination.is_junction()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "stay put"
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "stay put"


def test_move_junction_case_only_rename_preserves_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("stay put", encoding="utf-8")
    link = tmp_path / "link"
    _junction(link, target)

    result = service.move_path(str(link), str(tmp_path / "Link"))

    assert not result.startswith("Error:"), result
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Link", "target"]
    assert (tmp_path / "Link").is_junction()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "stay put"


@pytest.mark.parametrize("operation", [service.copy_path, service.move_path])
def test_broken_junction_destination_requires_overwrite_and_can_be_replaced(
    tmp_path: Path, operation
) -> None:
    target = tmp_path / "missing_target"
    target.mkdir()
    link = tmp_path / "broken_link"
    _junction(link, target)
    target.rmdir()

    source = tmp_path / "source"
    source.mkdir()
    (source / "fresh.txt").write_text("fresh", encoding="utf-8")

    denied = operation(str(source), str(link), overwrite=False)
    assert denied.startswith("Error: Destination already exists:"), denied
    assert link.is_junction()

    replaced = operation(str(source), str(link), overwrite=True)
    assert not replaced.startswith("Error:"), replaced
    assert not link.is_junction()
    assert (link / "fresh.txt").read_text(encoding="utf-8") == "fresh"


def test_move_broken_junction_moves_link(tmp_path: Path) -> None:
    target = tmp_path / "missing_target"
    target.mkdir()
    link = tmp_path / "broken_link"
    _junction(link, target)
    target.rmdir()
    destination = tmp_path / "relocated_broken_link"

    result = service.move_path(str(link), str(destination))

    assert not result.startswith("Error:"), result
    assert not link.is_junction()
    assert destination.is_junction()


@pytest.mark.parametrize("operation", [service.copy_path, service.move_path])
def test_overwrite_file_symlink_replaces_link_without_touching_target(
    tmp_path: Path, operation
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("original", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation requires Developer Mode or elevation: {exc}")

    source = tmp_path / "source.txt"
    source.write_text("replacement", encoding="utf-8")

    result = operation(str(source), str(link), overwrite=True)

    assert not result.startswith("Error:"), result
    assert not link.is_symlink()
    assert link.read_text(encoding="utf-8") == "replacement"
    assert target.read_text(encoding="utf-8") == "original"
