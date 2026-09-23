#!/usr/bin/env python3
"""Check whether this machine has a Python version accepted by pyproject.toml."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from windows_mcp.interpreter import InterpreterResolutionError, ensure_interpreter  # noqa: E402


def main() -> int:
    """Resolve a compatible interpreter and return a process exit code."""
    try:
        interpreter = ensure_interpreter()
    except InterpreterResolutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Python {interpreter.version_text} ({interpreter.source}) is compatible: "
        f"{interpreter.path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
