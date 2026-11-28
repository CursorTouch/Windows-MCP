"""Startup checks on the running interpreter.

Stdlib only: this runs before the server's third-party imports, because an
extension module without free-threading support re-enables the GIL when it is
imported, which would hide a free-threaded build from the check below.
"""

import sys


def exit_if_free_threaded() -> None:
    """Exit non-zero with a clear message on a free-threaded CPython build.

    Neither ``.python-version`` nor ``[tool.uv] python-preference`` stops uv
    from resolving a ``3.14+freethreaded`` interpreter, and the server is not
    supported there. Interpreters without ``sys._is_gil_enabled`` predate
    free-threading and are allowed through. The message goes to stderr because
    stdout carries the stdio protocol stream.
    """
    is_gil_enabled = getattr(sys, "_is_gil_enabled", None)
    if is_gil_enabled is None or is_gil_enabled():
        return
    print(
        "windows-mcp: cannot start -- free-threaded CPython (GIL disabled) is "
        "unsupported. Install a regular CPython 3.14 (e.g. `uv python install 3.14`) "
        "and recreate the environment, then restart the client.",
        file=sys.stderr,
    )
    sys.exit(1)
