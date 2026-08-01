"""Guard against bare `print()` in the server's source.

The default transport is stdio (`__main__.py`), where stdout *is* the
JSON-RPC channel -- as the codebase already notes elsewhere: "Written to
stderr because stdout carries the stdio protocol". Anything printed to
stdout is interleaved into that stream and corrupts the framing the client
is parsing.

This is a static scan rather than a runtime check, so it needs no Windows
imports and runs anywhere.

Two narrow exemptions, both deliberate:

* prints lexically inside an `if ...debug...:` block -- opt-in, off by
  default, and never reached in normal operation;
* `RunByHotKey`, a standalone hotkey-runner utility carried over from the
  upstream `uiautomation` library that the MCP server never invokes.

Anything else should use `logger`, which is already configured to write to
stderr.
"""

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "windows_mcp"

# Enclosing functions exempted wholesale. Keyed by name rather than line
# number so the allowlist survives edits elsewhere in the file.
EXEMPT_FUNCTIONS = {
    "threadFunc",  # nested inside RunByHotKey; upstream uiautomation utility
}


def _is_debug_gated(ancestors: list[ast.AST]) -> bool:
    """True if any enclosing `if` tests something named like a debug flag."""
    for node in ancestors:
        if isinstance(node, ast.If):
            test_source = ast.dump(node.test).lower()
            if "debug" in test_source:
                return True
    return False


def _enclosing_function(ancestors: list[ast.AST]) -> str | None:
    for node in reversed(ancestors):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


def _find_stdout_prints(path: Path) -> list[tuple[int, str | None]]:
    """Return (lineno, enclosing function) for each non-exempt print call.

    Only `ast.Call` nodes count, so prints appearing inside docstring
    examples are excluded for free -- those are strings, not calls.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[tuple[int, str | None]] = []

    def walk(node: ast.AST, ancestors: list[ast.AST]) -> None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            # `print(..., file=sys.stderr)` is not a stdout write.
            writes_elsewhere = any(kw.arg == "file" for kw in node.keywords)
            function = _enclosing_function(ancestors)
            if (
                not writes_elsewhere
                and not _is_debug_gated(ancestors)
                and function not in EXEMPT_FUNCTIONS
            ):
                offenders.append((node.lineno, function))

        for child in ast.iter_child_nodes(node):
            walk(child, ancestors + [node])

    walk(tree, [])
    return offenders


def _source_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


class TestNoStdoutWrites:
    def test_source_tree_was_found(self):
        """Fail loudly rather than passing vacuously on a bad path."""
        assert _source_files(), f"no Python sources under {SRC_ROOT}"

    @pytest.mark.parametrize(
        "path", _source_files(), ids=lambda p: str(p.relative_to(SRC_ROOT))
    )
    def test_no_bare_print(self, path: Path):
        offenders = _find_stdout_prints(path)
        assert offenders == [], "\n".join(
            f"{path.relative_to(SRC_ROOT)}:{line} in {func or '<module>'} "
            "writes to stdout, which carries the stdio JSON-RPC protocol; "
            "use logger instead"
            for line, func in offenders
        )
