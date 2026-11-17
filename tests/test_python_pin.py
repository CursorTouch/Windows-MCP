"""Guards for the interpreter request in `.python-version`.

The Claude Desktop extension (`server.type: "uv"`) and every `uv run` from a
checkout resolve their interpreter from `.python-version`. Two past failures
shape what that request may look like:

* Without a pin, uv picked the Microsoft Store Python, which patch-updates
  itself and leaves the venv half-installed (6e20039). Keeping uv on its own
  managed CPython is the job of `python-preference = "only-managed"` in
  `[tool.uv]`, not of the version string.
* An exact patch (`3.14.7`, from #391) cannot be resolved by a uv release
  that predates that CPython patch: uv embeds its download list, so older uv
  fails with "No interpreter found for Python 3.14.7 in managed
  installations" and the extension never starts.

An exact patch never protected against free-threaded builds either: with
uv 0.11.25, `uv python find 3.14.6` returns an installed
`3.14.6+freethreaded` when that is the only 3.14.6 present. So the request
is the bare minor version, and it must never name a free-threaded variant.
"""

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pin() -> str:
    return (REPO_ROOT / ".python-version").read_bytes().decode("utf-8")


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pin_is_a_single_lf_terminated_line():
    pin = _pin()
    assert pin.endswith("\n") and "\r" not in pin
    assert len(pin.splitlines()) == 1


def test_pin_is_a_bare_minor_version_not_an_exact_patch():
    # A bare minor resolves on any uv that knows at least one 3.MM build; an
    # exact patch needs a uv released after that patch.
    pin = _pin().strip()
    assert re.fullmatch(r"3\.\d+", pin), (
        f".python-version is {pin!r}; use the bare minor version (e.g. '3.14') "
        "so uv releases that predate the newest CPython patch can still resolve it"
    )


def test_pin_never_requests_a_free_threaded_build():
    pin = _pin().strip().lower()
    assert not pin.endswith("t") and "freethreaded" not in pin


def test_pin_matches_the_minimum_supported_python():
    requires = _pyproject()["project"]["requires-python"]
    match = re.fullmatch(r">=\s*(3\.\d+)", requires)
    assert match, f"unexpected requires-python form: {requires!r}"
    assert _pin().strip() == match.group(1)


def test_uv_only_uses_its_own_managed_python():
    # Keeps the Microsoft Store / system Python out of the venv, which the
    # exact-patch pin used to do only as a side effect.
    assert _pyproject()["tool"]["uv"].get("python-preference") == "only-managed"
