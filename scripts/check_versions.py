#!/usr/bin/env python3
"""Verify that every shipped version string agrees.

The project declares its version in four places, each consumed by a
different distribution channel:

    pyproject.toml              the PyPI package
    uv.lock                     the locked workspace member
    manifest.json               the Claude Desktop extension
    server.json packages[]      the MCP registry's PyPI package entry

These drift silently. At the v0.8.5 release `manifest.json` and
`server.json`'s package entry were both still on 0.8.1, two releases
behind `pyproject.toml`, because the 0.8.2 bump touched only
`pyproject.toml`. Nothing failed -- the extension simply kept advertising
a version that was no longer what shipped.

`server.json`'s TOP-LEVEL "version" is deliberately excluded. That field
identifies the registry entry rather than the PyPI package and is on its
own 1.x line; forcing it to match would be a version downgrade in the
registry. Only `packages[].version`, which must match what is published
to PyPI, is checked.

Run with no arguments to check internal consistency; pass a version (or a
`v`-prefixed release tag) to additionally require that everything matches
it.

    python scripts/check_versions.py
    python scripts/check_versions.py v0.8.6
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_UV_LOCK_WINDOWS_MCP = re.compile(
    r'^\[\[package\]\]\nname = "windows-mcp"\nversion = "([^"]+)"',
    re.MULTILINE,
)


def collect_versions(root: Path = REPO_ROOT) -> dict[str, str]:
    """Extract every declared version string, keyed by a human-readable label.

    Raises:
        ValueError: if a file is missing the version field entirely, which is
            just as much a packaging bug as a stale value.
    """
    versions: dict[str, str] = {}

    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        versions["pyproject.toml:project.version"] = pyproject["project"]["version"]
    except KeyError as exc:
        raise ValueError("pyproject.toml is missing [project] version") from exc

    lock_text = (root / "uv.lock").read_text(encoding="utf-8")
    match = _UV_LOCK_WINDOWS_MCP.search(lock_text)
    if match is None:
        raise ValueError("uv.lock has no [[package]] entry for windows-mcp")
    versions["uv.lock:windows-mcp"] = match.group(1)

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if "version" not in manifest:
        raise ValueError("manifest.json is missing a top-level version field")
    versions["manifest.json:version"] = manifest["version"]

    server = json.loads((root / "server.json").read_text(encoding="utf-8"))
    packages = server.get("packages", [])
    if not packages:
        raise ValueError("server.json declares no packages")
    for index, package in enumerate(packages):
        if "version" not in package:
            raise ValueError(f"server.json packages[{index}] is missing a version field")
        versions[f"server.json:packages[{index}].version"] = package["version"]

    return versions


def check(expected: str | None = None, root: Path = REPO_ROOT) -> list[str]:
    """Return a list of human-readable problems; empty means everything agrees."""
    versions = collect_versions(root)
    distinct = sorted(set(versions.values()))

    problems: list[str] = []
    if len(distinct) > 1:
        problems.append(f"version strings disagree: {', '.join(distinct)}")

    if expected is not None:
        target = expected.removeprefix("v")
        if any(version != target for version in versions.values()):
            problems.append(f"expected every version to be {target}")

    return problems


def main(argv: list[str]) -> int:
    expected = argv[1] if len(argv) > 1 else None

    try:
        versions = collect_versions()
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        print(f"error: could not read version metadata: {exc}", file=sys.stderr)
        return 1

    problems = check(expected)
    width = max(len(label) for label in versions)
    target = expected.removeprefix("v") if expected else None

    # Without an expected version there is no single "right" value to compare
    # against, so flag every string only when they fail to agree unanimously.
    consistent = len(set(versions.values())) == 1
    for label, version in versions.items():
        ok = version == target if target else consistent
        print(f"  {'ok ' if ok else 'BAD'}  {label:<{width}}  {version}")

    if problems:
        sys.stdout.flush()
        print(file=sys.stderr)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        print(
            "\nAll four files must be bumped together. server.json's top-level "
            '"version" is intentionally not checked -- it tracks the registry '
            "entry, not the PyPI package.",
            file=sys.stderr,
        )
        return 1

    scope = f" and match {target}" if target else ""
    print(f"\nAll {len(versions)} version strings agree{scope}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
