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


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("operation", [copy_path, move_path])
def test_file_onto_directory_is_refused(tmp_path, operation, overwrite):
    source = tmp_path / "report.txt"
    source.write_text("report", encoding="utf-8")
    destination = tmp_path / "Documents"
    nested = destination / "Taxes" / "2025.pdf"
    nested.parent.mkdir(parents=True)
    nested.write_text("keep me", encoding="utf-8")

    result = operation(str(source), str(destination), overwrite=overwrite)

    assert result.startswith(f"Error: Destination is a directory: {destination}.")
    assert f"use {destination / 'report.txt'} as the destination" in result
    assert source.read_text(encoding="utf-8") == "report"
    assert nested.read_text(encoding="utf-8") == "keep me"
    assert not (destination / "report.txt").exists()


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("operation", [copy_path, move_path])
def test_directory_onto_file_is_refused(tmp_path, operation, overwrite):
    source = tmp_path / "Documents"
    source.mkdir()
    (source / "thesis.docx").write_text("thesis", encoding="utf-8")
    destination = tmp_path / "report.txt"
    destination.write_text("report", encoding="utf-8")

    result = operation(str(source), str(destination), overwrite=overwrite)

    assert result.startswith(f"Error: Destination is a file: {destination}.")
    assert destination.read_text(encoding="utf-8") == "report"
    assert (source / "thesis.docx").read_text(encoding="utf-8") == "thesis"


@pytest.mark.parametrize("operation", [copy_path, move_path])
@pytest.mark.parametrize("kind", ["file", "directory"])
def test_overwrite_replaces_same_kind(tmp_path, operation, kind):
    source = tmp_path / "new"
    destination = tmp_path / "old"
    if kind == "file":
        source.write_text("new", encoding="utf-8")
        destination.write_text("old", encoding="utf-8")
    else:
        source.mkdir()
        (source / "new.txt").write_text("new", encoding="utf-8")
        destination.mkdir()
        (destination / "old.txt").write_text("old", encoding="utf-8")

    result = operation(str(source), str(destination), overwrite=True)

    assert not result.startswith("Error:"), result
    assert source.exists() is (operation is copy_path)
    if kind == "file":
        assert destination.read_text(encoding="utf-8") == "new"
    else:
        assert [p.name for p in destination.iterdir()] == ["new.txt"]
