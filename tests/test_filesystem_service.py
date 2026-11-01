import codecs
import os
import stat

import pytest

from windows_mcp.filesystem.service import (
    read_file,
    write_file,
    copy_path,
    move_path,
    delete_path,
    list_directory,
    search_files,
    get_file_info,
)


class TestReadFile:
    def test_read_entire_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world", encoding="utf-8")
        result = read_file(str(f))
        assert "hello world" in result

    def test_read_with_offset_and_limit(self, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
        result = read_file(str(f), offset=2, limit=2)
        assert "line2" in result
        assert "line3" in result
        assert "line4" not in result

    def test_read_nonexistent(self, tmp_path):
        result = read_file(str(tmp_path / "nope.txt"))
        assert "Error: File not found" in result

    def test_read_directory_path(self, tmp_path):
        result = read_file(str(tmp_path))
        assert "Error: Path is not a file" in result

    @pytest.mark.parametrize(
        "bom, codec",
        [
            (codecs.BOM_UTF8, "utf-8"),
            (codecs.BOM_UTF16_LE, "utf-16-le"),
            (codecs.BOM_UTF16_BE, "utf-16-be"),
        ],
        ids=["utf-8-sig", "utf-16-le", "utf-16-be"],
    )
    def test_read_decodes_bom(self, tmp_path, bom, codec):
        # Windows PowerShell 5.1's `>` writes UTF-16 LE; Notepad writes UTF-8 with a BOM.
        f = tmp_path / "bom.txt"
        f.write_bytes(bom + "héllo\r\nwörld\r\n".encode(codec))
        assert read_file(str(f)).endswith("\nhéllo\nwörld\n")
        assert read_file(str(f), offset=2, limit=1).endswith("of 2:\nwörld\n")

    def test_read_keeps_explicit_encoding_over_bom(self, tmp_path):
        f = tmp_path / "bom.txt"
        f.write_bytes(codecs.BOM_UTF8 + b"hi")
        assert read_file(str(f), encoding="latin-1").endswith("\nï»¿hi")


class TestWriteFile:
    def test_write_new_file(self, tmp_path):
        f = tmp_path / "out.txt"
        result = write_file(str(f), "content")
        assert "Written to" in result
        assert f.read_text(encoding="utf-8") == "content"

    def test_append_to_file(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_text("first", encoding="utf-8")
        write_file(str(f), " second", append=True)
        assert f.read_text(encoding="utf-8") == "first second"

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "a" / "b" / "c.txt"
        result = write_file(str(f), "deep")
        assert "Written to" in result
        assert f.read_text(encoding="utf-8") == "deep"

    @pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
    def test_write_uses_crlf_line_endings(self, tmp_path, newline):
        f = tmp_path / "out.txt"
        write_file(str(f), f"first{newline}second{newline}")
        assert f.read_bytes() == b"first\r\nsecond\r\n"
        assert read_file(str(f)).endswith("\nfirst\nsecond\n")

    @pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
    def test_append_uses_crlf_line_endings(self, tmp_path, newline):
        f = tmp_path / "out.txt"
        f.write_bytes(b"first\r\n")
        write_file(str(f), f"second{newline}", append=True)
        assert f.read_bytes() == b"first\r\nsecond\r\n"


class TestCopyPath:
    def test_copy_file(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        result = copy_path(str(src), str(dst))
        assert "Copied file" in result
        assert dst.read_text(encoding="utf-8") == "data"

    def test_copy_directory(self, tmp_path):
        src = tmp_path / "srcdir"
        src.mkdir()
        (src / "file.txt").write_text("inside", encoding="utf-8")
        dst = tmp_path / "dstdir"
        result = copy_path(str(src), str(dst))
        assert "Copied directory" in result
        assert (dst / "file.txt").read_text(encoding="utf-8") == "inside"

    def test_copy_source_not_found(self, tmp_path):
        result = copy_path(str(tmp_path / "nope"), str(tmp_path / "dst"))
        assert "Error: Source not found" in result

    def test_copy_destination_exists_no_overwrite(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        dst.write_text("existing", encoding="utf-8")
        result = copy_path(str(src), str(dst))
        assert "Error: Destination already exists" in result

    def test_copy_destination_exists_overwrite(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("new", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        dst.write_text("old", encoding="utf-8")
        result = copy_path(str(src), str(dst), overwrite=True)
        assert "Copied file" in result
        assert dst.read_text(encoding="utf-8") == "new"

    def test_copy_directory_overwrites_read_only_files(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "new.txt").write_text("new", encoding="utf-8")
        dst = tmp_path / "dst"
        dst.mkdir()
        old = dst / "old.txt"
        old.write_text("old", encoding="utf-8")
        os.chmod(old, stat.S_IREAD)
        result = copy_path(str(src), str(dst), overwrite=True)
        assert "Copied directory" in result
        assert [p.name for p in dst.iterdir()] == ["new.txt"]


class TestMovePath:
    def test_move_file(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        result = move_path(str(src), str(dst))
        assert "Moved" in result
        assert not src.exists()
        assert dst.read_text(encoding="utf-8") == "data"

    def test_move_source_not_found(self, tmp_path):
        result = move_path(str(tmp_path / "nope"), str(tmp_path / "dst"))
        assert "Error: Source not found" in result

    def test_move_destination_exists_no_overwrite(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dst = tmp_path / "dst.txt"
        dst.write_text("existing", encoding="utf-8")
        result = move_path(str(src), str(dst))
        assert "Error: Destination already exists" in result


class TestDeletePath:
    @pytest.mark.parametrize("recursive", [False, True])
    @pytest.mark.parametrize("kind", ["file", "directory", "dangling"])
    def test_delete_symlink_preserves_target(self, tmp_path, recursive, kind):
        target = tmp_path / "target"
        if kind == "file":
            target.write_text("keep me", encoding="utf-8")
        elif kind == "directory":
            target.mkdir()
            (target / "child.txt").write_text("keep me", encoding="utf-8")
        link = tmp_path / "link"
        try:
            link.symlink_to(target, target_is_directory=kind == "directory")
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Creating symbolic links requires Windows privileges")
            raise

        result = delete_path(str(link), recursive=recursive)

        assert "Deleted file" in result
        assert not link.is_symlink()
        if kind == "file":
            assert target.read_text(encoding="utf-8") == "keep me"
        elif kind == "directory":
            assert (target / "child.txt").read_text(encoding="utf-8") == "keep me"

    @pytest.mark.skipif(os.name != "nt", reason="Windows junctions")
    @pytest.mark.parametrize("recursive", [False, True])
    @pytest.mark.parametrize("dangling", [False, True])
    def test_delete_junction_preserves_target(self, tmp_path, recursive, dangling):
        import _winapi

        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        _winapi.CreateJunction(str(target), str(link))
        if dangling:
            target.rmdir()
        else:
            (target / "child.txt").write_text("keep me", encoding="utf-8")

        result = delete_path(str(link), recursive=recursive)

        assert "Deleted directory" in result
        assert not link.is_junction()
        if not dangling:
            assert (target / "child.txt").read_text(encoding="utf-8") == "keep me"

    def test_delete_file(self, tmp_path):
        f = tmp_path / "del.txt"
        f.write_text("bye", encoding="utf-8")
        result = delete_path(str(f))
        assert "Deleted file" in result
        assert not f.exists()

    def test_delete_empty_dir(self, tmp_path):
        d = tmp_path / "emptydir"
        d.mkdir()
        result = delete_path(str(d))
        assert "Deleted directory" in result
        assert not d.exists()

    def test_delete_nonempty_dir_without_recursive(self, tmp_path):
        d = tmp_path / "fulldir"
        d.mkdir()
        (d / "file.txt").write_text("x", encoding="utf-8")
        result = delete_path(str(d), recursive=False)
        assert "Error: Directory is not empty" in result
        assert d.exists()

    def test_delete_nonempty_dir_recursive(self, tmp_path):
        d = tmp_path / "fulldir"
        d.mkdir()
        (d / "file.txt").write_text("x", encoding="utf-8")
        result = delete_path(str(d), recursive=True)
        assert "Deleted directory" in result
        assert not d.exists()

    def test_delete_recursive_removes_read_only_files(self, tmp_path):
        # Git marks .git/objects files read-only, which blocks a plain rmtree on Windows.
        d = tmp_path / "repo"
        pack = d / ".git" / "objects" / "pack" / "pack-1.pack"
        pack.parent.mkdir(parents=True)
        pack.write_bytes(b"x")
        os.chmod(pack, stat.S_IREAD)
        result = delete_path(str(d), recursive=True)
        assert "Deleted directory" in result
        assert not d.exists()

    def test_delete_not_found(self, tmp_path):
        result = delete_path(str(tmp_path / "ghost"))
        assert "Error: Path not found" in result


class TestListDirectory:
    @pytest.mark.parametrize("pattern", [None, "*.txt"])
    @pytest.mark.parametrize("show_hidden", [False, True])
    def test_recursive_hidden_ancestors(self, tmp_path, pattern, show_hidden):
        hidden = tmp_path / ".hidden" / "nested"
        hidden.mkdir(parents=True)
        (hidden / "secret.txt").write_text("hidden", encoding="utf-8")
        visible = tmp_path / "visible"
        visible.mkdir()
        (visible / "public.txt").write_text("visible", encoding="utf-8")
        (visible / ".private.txt").write_text("hidden", encoding="utf-8")

        result = list_directory(
            str(tmp_path), pattern=pattern, recursive=True, show_hidden=show_hidden
        )

        assert "public.txt" in result
        assert ("secret.txt" in result) is show_hidden
        assert (".private.txt" in result) is show_hidden

    def test_recursive_listing_inside_explicit_hidden_root(self, tmp_path):
        root = tmp_path / ".config"
        root.mkdir()
        (root / "settings.txt").write_text("visible", encoding="utf-8")

        assert "settings.txt" in list_directory(str(root), recursive=True)

    def test_list_basic(self, tmp_path):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        result = list_directory(str(tmp_path))
        assert "a.txt" in result
        assert "b.txt" in result

    def test_list_with_pattern(self, tmp_path):
        (tmp_path / "hello.py").write_text("x", encoding="utf-8")
        (tmp_path / "hello.txt").write_text("x", encoding="utf-8")
        result = list_directory(str(tmp_path), pattern="*.py")
        assert "hello.py" in result
        assert "hello.txt" not in result

    def test_list_hides_hidden_by_default(self, tmp_path):
        (tmp_path / ".hidden").write_text("x", encoding="utf-8")
        (tmp_path / "visible").write_text("x", encoding="utf-8")
        result = list_directory(str(tmp_path))
        assert ".hidden" not in result
        assert "visible" in result

    def test_list_shows_hidden_when_enabled(self, tmp_path):
        (tmp_path / ".hidden").write_text("x", encoding="utf-8")
        result = list_directory(str(tmp_path), show_hidden=True)
        assert ".hidden" in result

    def test_list_empty(self, tmp_path):
        result = list_directory(str(tmp_path))
        assert "empty" in result.lower()

    def test_list_not_found(self, tmp_path):
        result = list_directory(str(tmp_path / "nope"))
        assert "Error: Directory not found" in result

    def test_dirs_listed_before_files(self, tmp_path):
        (tmp_path / "z_file.txt").write_text("x", encoding="utf-8")
        (tmp_path / "a_dir").mkdir()
        result = list_directory(str(tmp_path))
        dir_pos = result.index("a_dir")
        file_pos = result.index("z_file.txt")
        assert dir_pos < file_pos


class TestSearchFiles:
    def test_search_basic(self, tmp_path):
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        result = search_files(str(tmp_path), "*.py")
        assert "a.py" in result
        assert "b.txt" not in result

    def test_search_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("x", encoding="utf-8")
        result = search_files(str(tmp_path), "*.py", recursive=True)
        assert "deep.py" in result

    def test_search_no_matches(self, tmp_path):
        result = search_files(str(tmp_path), "*.xyz")
        assert "No matches found" in result

    def test_search_path_not_found(self, tmp_path):
        result = search_files(str(tmp_path / "nope"), "*.py")
        assert "Error: Search path not found" in result


class TestGetFileInfo:
    def test_file_info(self, tmp_path):
        f = tmp_path / "info.txt"
        f.write_text("hello", encoding="utf-8")
        result = get_file_info(str(f))
        assert "Type: File" in result
        assert "Extension: .txt" in result
        assert "5" in result  # 5 bytes

    def test_dir_info(self, tmp_path):
        d = tmp_path / "mydir"
        d.mkdir()
        (d / "child.txt").write_text("x", encoding="utf-8")
        result = get_file_info(str(d))
        assert "Type: Directory" in result
        assert "1 files" in result

    def test_not_found(self, tmp_path):
        result = get_file_info(str(tmp_path / "nope"))
        assert "Error: Path not found" in result

    @pytest.mark.parametrize("dangling", [False, True])
    def test_symlink_info_reports_link(self, tmp_path, dangling):
        target = tmp_path / "target.txt"
        target.write_text("hello", encoding="utf-8")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(target)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Creating symbolic links requires Windows privileges")
            raise
        if dangling:
            target.unlink()

        result = get_file_info(str(link))

        assert f"Path: {link}" in result
        assert "Type: Symlink" in result
        assert f"Link target: {target}" in result

    @pytest.mark.skipif(os.name != "nt", reason="Windows junctions")
    @pytest.mark.parametrize("dangling", [False, True])
    def test_junction_info_reports_link(self, tmp_path, dangling):
        import _winapi

        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        _winapi.CreateJunction(str(target), str(link))
        if dangling:
            target.rmdir()

        result = get_file_info(str(link))

        assert f"Path: {link}" in result
        assert "Type: Junction" in result
        assert f"Link target: {target}" in result
