"""Issue #506: the Move tool must warn that XAML shell surfaces ignore drag."""

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "windows_mcp" / "tools" / "input.py"
README = Path(__file__).resolve().parents[1] / "README.md"


def test_move_description_mentions_xaml_shell_surfaces():
    text = SRC.read_text(encoding="utf-8")
    assert "XAML shell surfaces such as Start menu" in text


def test_readme_documents_xaml_drag_limit():
    text = README.read_text(encoding="utf-8")
    assert "Drag is not supported on XAML shell surfaces" in text
