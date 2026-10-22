import pytest

from windows_mcp.filesystem.service import copy_path, move_path


@pytest.mark.parametrize("operation", [copy_path, move_path])
@pytest.mark.parametrize(
    "layout", ["same_file", "same_directory", "parent_directory", "child_directory"]
)
def test_overlapping_paths_preserve_source(tmp_path, operation, layout):
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "payload.txt"
    payload.write_text("preserve me", encoding="utf-8")
    if layout == "same_file":
        source = payload
        destination = payload
    elif layout == "same_directory":
        destination = source
    elif layout == "parent_directory":
        destination = source
        source = payload
    else:
        destination = source / "child"
    result = operation(str(source), str(destination), overwrite=True)
    assert result.startswith("Error:")
    assert payload.read_text(encoding="utf-8") == "preserve me"
    assert not (tmp_path / "source" / "child").exists()
