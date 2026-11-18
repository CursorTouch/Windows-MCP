"""Guards for the interpreter request in `.python-version`.

The Claude Desktop extension (`server.type: "uv"`) and every `uv run` from a
checkout resolve their interpreter from `.python-version`. Two past failures
shape what that request may look like:

* Without a pin, uv picked the Microsoft Store Python, which patch-updates
  itself and leaves the venv half-installed (6e20039). Steering uv to its own
  managed CPython is the job of `python-preference = "managed"` in
  `[tool.uv]`, not of the version string.
* An exact patch (`3.14.7`, from #391) cannot be resolved by a uv release
  that predates that CPython patch: uv embeds its download list, so older uv
  fails with "No interpreter found for Python 3.14.7 in managed
  installations" and the extension never starts.

No version string keeps free-threaded builds out: with uv 0.11.25,
`uv python find 3.14.6` returns an installed `3.14.6+freethreaded` when that
is the only 3.14.6 present. The server refuses those at startup instead
(`windows_mcp.interpreter`, tests/test_free_threaded_guard.py), so the request
here is just the bare minor version.
"""

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pin() -> str:
    return (REPO_ROOT / ".python-version").read_bytes().decode("utf-8")


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pin_is_a_single_lf_terminated_line():
    pin = _pin()
    assert pin.endswith("\n") and "\r" not in pin
    assert len(pin.splitlines()) == 1


def test_git_checks_out_the_pin_with_lf_endings():
    # core.autocrlf=true would otherwise rewrite the pin to CRLF on checkout and
    # fail the single-LF-line check above on a fresh Windows clone.
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    result = subprocess.run(
        [git, "check-attr", "text", "eol", "--", ".python-version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"not a git checkout: {result.stderr.strip()}")
    assert result.stdout.splitlines() == [
        ".python-version: text: set",
        ".python-version: eol: lf",
    ]


def test_pin_is_a_bare_minor_version_not_an_exact_patch():
    # A bare minor resolves on any uv that knows at least one 3.MM build; an
    # exact patch needs a uv released after that patch.
    pin = _pin().strip()
    assert re.fullmatch(r"3\.\d+", pin), (
        f".python-version is {pin!r}; use the bare minor version (e.g. '3.14') "
        "so uv releases that predate the newest CPython patch can still resolve it"
    )


def test_pin_matches_the_minimum_supported_python():
    requires = _pyproject()["project"]["requires-python"]
    match = re.fullmatch(r">=\s*(3\.\d+)", requires)
    assert match, f"unexpected requires-python form: {requires!r}"
    assert _pin().strip() == match.group(1)


def test_uv_prefers_its_own_managed_python_but_can_fall_back():
    # "managed" picks uv's own CPython over the Microsoft Store / system Python
    # whenever one is installed or downloadable, yet still lets an offline
    # machine with only a system 3.14 start; "only-managed" would refuse it.
    assert _pyproject()["tool"]["uv"].get("python-preference") == "managed"
