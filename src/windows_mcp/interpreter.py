"""Discover compatible Python interpreters and optionally install one via uv.

This module deliberately uses only the Python standard library.  ``packaging`` is
used when it happens to be available, but the fallback implementation keeps the
project discovery script usable in a bare interpreter as well.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:  # packaging is optional; the fallback below covers the supported operators.
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version
except ImportError:  # pragma: no cover - exercised on machines without packaging.
    InvalidSpecifier = InvalidVersion = None  # type: ignore[assignment]
    SpecifierSet = Version = None  # type: ignore[assignment]


_PACKAGING_AVAILABLE = SpecifierSet is not None and Version is not None
_SPECIFIER_RE = re.compile(
    r"^\s*(?P<operator>~=|==|!=|<=|>=|<|>)?\s*v?"
    r"(?P<release>\d+(?:\.\d+)*)(?P<wildcard>\.\*)?\s*$"
)
_VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
_INSTALL_SPEC_RE = re.compile(
    r"^\s*(?P<operator>~=|==|!=|<=|>=|<|>)?\s*v?"
    r"(?P<release>\d+(?:\.\d+)*)(?:\.\*)?\s*$"
)


@dataclass(frozen=True, slots=True)
class InterpreterInfo:
    """A discovered Python interpreter and its relationship to the project."""

    path: Path
    version: tuple[int, ...]
    source: str
    satisfies_requirement: bool
    distribution: str | None = None

    @property
    def version_text(self) -> str:
        """Return the interpreter version in dotted form."""
        return ".".join(str(part) for part in self.version)


class InterpreterResolutionError(RuntimeError):
    """Raised when no compatible interpreter can be selected or installed."""


def _default_pyproject_path() -> Path:
    return Path(__file__).resolve().parents[2] / "pyproject.toml"


def parse_requires_python(pyproject_path: Path | None = None) -> str | None:
    """Read ``project.requires-python`` from pyproject.toml.

    Args:
        pyproject_path: Explicit file to inspect.  When omitted, the repository
            root adjacent to this source tree is used.

    Returns:
        The requirement string, or ``None`` when the file or field is absent.
    """
    path = pyproject_path or _default_pyproject_path()
    if not path.is_file():
        return None

    import tomllib

    with path.open("rb") as handle:
        data = tomllib.load(handle)
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    requirement = project.get("requires-python")
    if requirement is None:
        return None
    return str(requirement).strip() or None


def _version_key(version: tuple[int, ...], width: int = 8) -> tuple[int, ...]:
    return version + (0,) * max(0, width - len(version))


def _compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    left_key = _version_key(left, width)
    right_key = _version_key(right, width)
    if left_key < right_key:
        return -1
    if left_key > right_key:
        return 1
    return 0


def _parse_specifier(text: str) -> tuple[str, tuple[int, ...], bool] | None:
    match = _SPECIFIER_RE.fullmatch(text)
    if match is None:
        return None
    release = tuple(int(part) for part in match.group("release").split("."))
    return match.group("operator") or "==", release, bool(match.group("wildcard"))


def _matches_specifier(
    version: tuple[int, ...], operator: str, release: tuple[int, ...], wildcard: bool
) -> bool:
    if wildcard and operator not in {"==", "!="}:
        raise ValueError(f"wildcards are not supported with {operator!r}")

    if operator in {"==", "!="} and wildcard:
        version_prefix = _version_key(version, len(release))[: len(release)]
        matches = version_prefix == release
        return not matches if operator == "!=" else matches

    comparison = _compare_versions(version, release)
    if operator == "==":
        return comparison == 0
    if operator == "!=":
        return comparison != 0
    if operator == ">=":
        return comparison >= 0
    if operator == ">":
        return comparison > 0
    if operator == "<=":
        return comparison <= 0
    if operator == "<":
        return comparison < 0
    if operator == "~=":
        if len(release) < 2:
            raise ValueError("~= requires at least a major and minor version")
        upper = list(release[:-1])
        upper[-1] += 1
        return comparison >= 0 and _compare_versions(version, tuple(upper)) < 0
    raise ValueError(f"unsupported version operator: {operator!r}")


def _satisfies_fallback(version: tuple[int, ...], spec: str) -> bool:
    if not spec.strip():
        return True
    for alternative in spec.split("||"):
        if not alternative.strip():
            continue
        clause_ok = True
        for raw_clause in alternative.split(","):
            clause = raw_clause.strip()
            if not clause:
                continue
            parsed = _parse_specifier(clause)
            if parsed is None:
                raise ValueError(f"unsupported version constraint: {clause!r}")
            operator, release, wildcard = parsed
            if not _matches_specifier(version, operator, release, wildcard):
                clause_ok = False
                break
        if clause_ok:
            return True
    return False


def satisfies(version: tuple[int, ...], spec: str) -> bool:
    """Return whether ``version`` satisfies a PEP 440-style requirement.

    ``packaging.specifiers`` is preferred when installed.  The fallback supports
    ``>=``, ``>``, ``<=``, ``<``, ``==``, ``!=``, ``~=``, comma-separated
    clauses, and ``||`` alternatives.
    """
    if not spec.strip():
        return True

    for alternative in spec.split("||"):
        alternative = alternative.strip()
        if not alternative:
            continue
        if _PACKAGING_AVAILABLE:
            try:
                parsed_version = Version(".".join(str(part) for part in version))
                if parsed_version in SpecifierSet(alternative):
                    return True
                continue
            except (InvalidSpecifier, InvalidVersion):
                pass
        if _satisfies_fallback(version, alternative):
            return True
    return False


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _run_capture(command: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        creationflags=_creation_flags(),
    )


def find_uv() -> Path | None:
    """Return the uv executable path, or ``None`` when uv is unavailable."""
    names = ["uv.exe", "uv"] if os.name == "nt" else ["uv", "uv.exe"]
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)

    interpreter_dir = Path(sys.executable).resolve().parent
    for name in names:
        candidate = interpreter_dir / name
        if candidate.is_file():
            return candidate
    return None


def get_uv_version() -> str | None:
    """Return uv's reported version, or ``None`` when uv cannot be queried."""
    uv = find_uv()
    if uv is None:
        return None
    try:
        completed = _run_capture([str(uv), "--version"], timeout=10.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else None


def _version_from_text(text: str) -> tuple[int, ...] | None:
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def _probe_python(path: Path) -> tuple[int, ...] | None:
    if not path.is_file():
        return None
    code = "import sys; print('.'.join(str(part) for part in sys.version_info[:3]))"
    try:
        completed = _run_capture([str(path), "-I", "-c", code], timeout=10.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return _version_from_text(completed.stdout.strip())


def _uv_interpreters() -> list[tuple[Path, tuple[int, ...], str]]:
    uv = find_uv()
    if uv is None:
        return []
    try:
        completed = _run_capture(
            [str(uv), "python", "list", "--only-installed", "--no-config"],
            timeout=30.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []

    interpreters: list[tuple[Path, tuple[int, ...], str]] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            continue
        distribution, raw_path = fields
        version = _version_from_text(distribution)
        if version is None:
            continue
        interpreters.append((Path(raw_path.strip().strip('"')), version, distribution))
    return interpreters


def _is_windows_store_alias(path: Path) -> bool:
    return os.name == "nt" and any(part.lower() == "windowsapps" for part in path.parts)


def _path_python_candidates() -> Iterable[Path]:
    names = ["python.exe", "python3.exe"] if os.name == "nt" else ["python", "python3"]
    for name in names:
        found = shutil.which(name)
        if found and not _is_windows_store_alias(Path(found)):
            yield Path(found)


def _interpreter_sort_key(info: InterpreterInfo) -> tuple[tuple[int, ...], int]:
    source_priority = {"uv": 0, "path": 1, "current": 2}.get(info.source.lower(), 3)
    return _version_key(info.version), -source_priority


def discover_interpreters() -> list[InterpreterInfo]:
    """Enumerate installed uv and PATH interpreters.

    Each returned item records its executable path, parsed release, source, and
    whether it satisfies the repository's ``requires-python`` constraint.
    """
    requirement = parse_requires_python()
    discovered: list[InterpreterInfo] = []
    seen: set[str] = set()

    def add(path: Path, source: str, distribution: str | None = None) -> None:
        try:
            key = os.path.normcase(str(path.resolve(strict=False)))
        except OSError:
            key = os.path.normcase(str(path))
        if key in seen:
            return
        seen.add(key)

        version = _probe_python(path)
        if version is None:
            return
        meets = True if requirement is None else satisfies(version, requirement)
        discovered.append(
            InterpreterInfo(
                path=path,
                version=version,
                source=source,
                satisfies_requirement=meets,
                distribution=distribution,
            )
        )

    for path, version, distribution in _uv_interpreters():
        if not path.is_file():
            continue
        try:
            key = os.path.normcase(str(path.resolve(strict=False)))
        except OSError:
            key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        requirement_ok = True if requirement is None else satisfies(version, requirement)
        discovered.append(
            InterpreterInfo(
                path=path,
                version=version,
                source="uv",
                satisfies_requirement=requirement_ok,
                distribution=distribution,
            )
        )

    for path in _path_python_candidates():
        add(path, "path")
    add(Path(sys.executable), "current")

    discovered.sort(key=_interpreter_sort_key, reverse=True)
    return discovered


def format_interpreters(interpreters: Iterable[InterpreterInfo]) -> str:
    """Format interpreter information as a compact plain-text table."""
    rows = list(interpreters)
    if not rows:
        return "  (no Python interpreters found)"

    headers = ("Version", "Source", "Meets", "Path")
    body = [
        (
            info.version_text,
            info.source,
            "yes" if info.satisfies_requirement else "no",
            str(info.path),
        )
        for info in rows
    ]
    widths = [max(len(headers[index]), *(len(row[index]) for row in body)) for index in range(4)]
    lines = [
        "  "
        + "  ".join(
            headers[index].ljust(widths[index]) for index in range(len(headers))
        ),
        "  " + "  ".join("-" * width for width in widths),
    ]
    lines.extend(
        "  "
        + "  ".join(row[index].ljust(widths[index]) for index in range(len(headers)))
        for row in body
    )
    return "\n".join(lines)


def _derive_install_spec(requirement: str) -> str | None:
    lower_bounds: list[str] = []
    upper_only: list[str] = []
    for alternative in requirement.split("||"):
        for raw_clause in alternative.split(","):
            clause = raw_clause.strip()
            match = _INSTALL_SPEC_RE.fullmatch(clause)
            if match is None:
                continue
            operator = match.group("operator") or "=="
            release = match.group("release")
            if operator in {"==", "~="}:
                return release
            if operator in {">=", ">"}:
                lower_bounds.append(release)
            elif operator in {"<=", "<"}:
                upper_only.append(release)
    candidates = lower_bounds or upper_only
    if not candidates:
        return None
    return candidates[0]


def _manual_guidance(requirement: str | None) -> str:
    install_spec = _derive_install_spec(requirement) if requirement else None
    if install_spec is None:
        install_command = "uv python install <compatible-version>"
    else:
        install_command = f"uv python install {install_spec}"
    return f"Manual command: {install_command}\nThen run: uv run windows-mcp serve"


def _raise_resolution_error(message: str, requirement: str | None) -> None:
    guidance = _manual_guidance(requirement)
    print(f"{message}\n{guidance}", file=sys.stderr)
    raise InterpreterResolutionError(f"{message} {guidance}")


def ensure_interpreter(
    *, assume_yes: bool = False, interactive: bool | None = None
) -> InterpreterInfo:
    """Return a compatible interpreter, installing one with explicit consent.

    The only installation command this function can issue is ``uv python install
    <spec>``.  It never updates, replaces, uninstalls, or selects an existing
    system interpreter.
    """
    requirement = parse_requires_python()
    interpreters = discover_interpreters()
    compatible = [info for info in interpreters if info.satisfies_requirement]
    if compatible:
        return compatible[0]

    print("Available Python interpreters:", file=sys.stderr)
    print(format_interpreters(interpreters), file=sys.stderr)

    if requirement is None:
        _raise_resolution_error(
            "No usable Python interpreter was found and pyproject.toml declares no requirement.",
            None,
        )

    install_spec = _derive_install_spec(requirement)
    if install_spec is None:
        _raise_resolution_error(
            f"Could not derive an installable Python version from requires-python {requirement!r}.",
            requirement,
        )

    uv = find_uv()
    if uv is None:
        _raise_resolution_error(
            "No compatible Python interpreter was found and uv is not available on PATH.",
            requirement,
        )

    interactive_enabled = sys.stdin.isatty() if interactive is None else interactive
    if not assume_yes and not interactive_enabled:
        _raise_resolution_error(
            f"No Python interpreter satisfies {requirement} and installation was not authorized.",
            requirement,
        )

    if not assume_yes:
        prompt = (
            f"未找到满足 {requirement} 的 Python。"
            f"是否让 uv 安装一个独立的 Python {install_spec}？\n"
            "(不会修改或升级任何现有解释器) [y/N] "
        )
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            _raise_resolution_error(
                f"Interpreter installation was not authorized for {requirement}.",
                requirement,
            )

    print(
            f"Installing Python {install_spec} into uv's managed interpreter directory...",
            file=sys.stderr,
        )
    try:
        completed = subprocess.run(
            [str(uv), "python", "install", install_spec],
            check=False,
            creationflags=_creation_flags(),
        )
    except OSError as exc:
        _raise_resolution_error(
            f"uv could not be started ({exc}).",
            requirement,
        )
    if completed.returncode != 0:
        _raise_resolution_error(
            f"uv python install {install_spec} failed with exit code {completed.returncode}.",
            requirement,
        )

    compatible = [info for info in discover_interpreters() if info.satisfies_requirement]
    if compatible:
        return compatible[0]
    _raise_resolution_error(
        f"uv installed Python {install_spec}, but no interpreter satisfying {requirement} was found.",
        requirement,
    )
