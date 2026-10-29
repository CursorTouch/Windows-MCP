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


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_move_renames_case_only(tmp_path, kind):
    source = tmp_path / "report"
    if kind == "file":
        source.write_text("keep", encoding="utf-8")
    else:
        source.mkdir()
    result = move_path(str(source), str(tmp_path / "Report"))
    assert not result.startswith("Error:"), result
    assert [p.name for p in tmp_path.iterdir()] == ["Report"]
    if kind == "file":
        assert (tmp_path / "Report").read_text(encoding="utf-8") == "keep"
