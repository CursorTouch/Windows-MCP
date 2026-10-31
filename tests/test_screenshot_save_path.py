"""Issue #496: Snapshot/Screenshot can write the returned image to a file."""

import pytest

from windows_mcp.desktop.screenshot_file import save_screenshot

PNG = b"\x89PNG\r\n\x1a\nfake"


def test_saves_to_explicit_file(tmp_path):
    target = tmp_path / "shot.png"
    assert save_screenshot(PNG, str(target)) == str(target.resolve())
    assert target.read_bytes() == PNG


def test_adds_png_suffix(tmp_path):
    saved = save_screenshot(PNG, str(tmp_path / "shot"))
    assert saved.endswith("shot.png")


def test_directory_gets_timestamped_name(tmp_path):
    saved = save_screenshot(PNG, str(tmp_path))
    assert saved.startswith(str(tmp_path.resolve()))
    assert "screenshot-" in saved and saved.endswith(".png")


def test_trailing_separator_creates_directory(tmp_path):
    saved = save_screenshot(PNG, str(tmp_path / "new") + "/")
    assert (tmp_path / "new").is_dir()
    assert saved.endswith(".png")


def test_creates_missing_parent(tmp_path):
    target = tmp_path / "a" / "b" / "shot.png"
    save_screenshot(PNG, str(target))
    assert target.exists()


def test_never_overwrites(tmp_path):
    target = tmp_path / "shot.png"
    target.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        save_screenshot(PNG, str(target))
    assert target.read_bytes() == b"old"


@pytest.mark.parametrize("path", ["relative/shot.png", "", "   "])
def test_rejects_relative_or_empty(path):
    with pytest.raises(ValueError):
        save_screenshot(PNG, path)


def test_rejects_missing_image(tmp_path):
    with pytest.raises(ValueError):
        save_screenshot(None, str(tmp_path / "shot.png"))
