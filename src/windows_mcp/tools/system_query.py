"""Structured, read-only Windows and project queries."""

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import psutil
from fastmcp import Context
from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics

_MAX_ITEMS = 200
_MAX_TEXT_BYTES = 65_536
_MAX_COMMAND_OUTPUT_BYTES = 65_536
_COMMAND_TIMEOUT_SECONDS = 5
_CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
_BLOCKED_PARTS = {
    ".env",
    ".git",
    "credential",
    "credentials",
    "id_rsa",
    "private_key",
    "secret",
    "secrets",
    "token",
    "tokens",
}
class QueryTimeoutError(RuntimeError):
    """Controlled timeout that affects only the current tool call."""


_ALLOWED_TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".ps1",
    ".py",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
}


def _project_root() -> Path:
    configured = os.environ.get("WINDOWS_MCP_PROJECT_ROOT")
    return Path(configured or Path.cwd()).resolve()


def _contains_blocked_part(path: Path) -> bool:
    lowered = [part.casefold() for part in path.parts]
    return any(any(blocked in part for blocked in _BLOCKED_PARTS) for part in lowered)


def _safe_project_path(target: str | None) -> Path:
    root = _project_root()
    candidate = (root / (target or ".")).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("A consulta deve permanecer dentro da pasta do projeto.")
    if _contains_blocked_part(candidate.relative_to(root)):
        raise ValueError("Caminho protegido contra leitura de credenciais ou dados de segurança.")
    return candidate


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> list[int]:
    terminated: list[int] = []
    try:
        descendants = psutil.Process(process.pid).children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        descendants = []
    for child in reversed(descendants):
        try:
            child.kill()
            terminated.append(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    try:
        process.kill()
        terminated.append(process.pid)
    except OSError:
        pass
    try:
        process.wait(timeout=1)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return terminated


def _read_limited(path: Path) -> str:
    try:
        raw = path.read_bytes()[:_MAX_COMMAND_OUTPUT_BYTES]
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace").strip()


def _completed(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = _COMMAND_TIMEOUT_SECONDS,
) -> str:
    """Run an allowlisted command without inherited pipes or a shell."""
    timeout = max(1, min(int(timeout), 20))
    with tempfile.TemporaryDirectory(prefix="windows-mcp-query-") as temp_dir:
        stdout_path = Path(temp_dir) / "stdout.txt"
        stderr_path = Path(temp_dir) / "stderr.txt"
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                args,
                cwd=str(cwd) if cwd else None,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                creationflags=_CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            try:
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                terminated = _terminate_process_tree(process)
                raise QueryTimeoutError(
                    f"Consulta excedeu {timeout}s e foi encerrada de forma isolada; "
                    f"processos encerrados: {terminated}. A sessão MCP permanece ativa."
                ) from exc
        stdout_text = _read_limited(stdout_path)
        stderr_text = _read_limited(stderr_path)
    output = stdout_text or stderr_text
    if return_code != 0:
        raise RuntimeError(f"Consulta falhou com código {return_code}: {output[:1000]}")
    return output

def _tail_text(path: Path, limit: int) -> str:
    if path.suffix.casefold() not in _ALLOWED_TEXT_SUFFIXES:
        raise ValueError("Tipo de arquivo não permitido para leitura estruturada.")
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > _MAX_TEXT_BYTES:
            handle.seek(max(0, size - _MAX_TEXT_BYTES))
        raw = handle.read(_MAX_TEXT_BYTES)
    text = raw.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-limit:])


def _tunnel_status(root: Path) -> dict[str, object]:
    executable = root / ".tunnel-client" / "bin" / "tunnel-client.exe"
    if not executable.is_file():
        raise FileNotFoundError("tunnel-client não encontrado no projeto.")
    raw = _completed(
        [str(executable), "runtimes", "status", "windows-mcp-gpt", "--json"],
        cwd=root,
        timeout=5,
    )
    data = json.loads(raw)
    process = data.get("process") or {}
    return {
        "alias": data.get("alias"),
        "ready": data.get("ready"),
        "healthy": data.get("healthy"),
        "runtime_state": data.get("runtime_state"),
        "process_running": data.get("process_running"),
        "pid": process.get("pid"),
        "started_at": process.get("started_at"),
        "error": data.get("error") or data.get("remote_error") or "",
    }


def _program_version(name: str, root: Path) -> dict[str, str]:
    normalized = name.casefold().strip()
    if normalized == "python":
        return {"program": "python", "version": sys.version.split()[0]}
    if normalized == "windows-mcp":
        try:
            installed = version("windows-mcp")
        except PackageNotFoundError:
            installed = "desconhecida"
        return {"program": "windows-mcp", "version": installed}
    commands = {
        "git": [shutil.which("git") or "git", "--version"],
        "uv": [shutil.which("uv") or "uv", "--version"],
    }
    if normalized == "tunnel-client":
        executable = root / ".tunnel-client" / "bin" / "tunnel-client.exe"
        first_line = _completed([str(executable), "run", "--help"], timeout=5).splitlines()[0]
        return {"program": "tunnel-client", "version": first_line}
    if normalized not in commands:
        raise ValueError("Programa não permitido. Use: git, python, uv, windows-mcp ou tunnel-client.")
    return {"program": normalized, "version": _completed(commands[normalized], timeout=3)}


def _resolve_git_dir(root: Path) -> Path:
    marker = root / ".git"
    if marker.is_dir():
        return marker
    if marker.is_file():
        raw = marker.read_text(encoding="utf-8", errors="replace").strip()
        if raw.casefold().startswith("gitdir:"):
            return (root / raw.split(":", 1)[1].strip()).resolve()
    raise FileNotFoundError("Metadados Git não encontrados no projeto.")


def _read_git_head(git_dir: Path) -> tuple[str, str]:
    head = (git_dir / "HEAD").read_text(encoding="ascii", errors="replace").strip()
    if not head.startswith("ref: "):
        return "(detached)", head
    reference = head[5:].strip()
    branch = reference.removeprefix("refs/heads/")
    ref_path = git_dir / reference
    if ref_path.is_file():
        return branch, ref_path.read_text(encoding="ascii", errors="replace").strip()
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="ascii", errors="replace").splitlines():
            if line and not line.startswith(("#", "^")):
                sha, _, name = line.partition(" ")
                if name == reference:
                    return branch, sha
    return branch, ""


def _read_git_index(root: Path, git_dir: Path) -> dict[str, object]:
    index_path = git_dir / "index"
    if not index_path.is_file():
        return {"index_version": None, "index_entries": 0, "possibly_modified": [], "missing": [], "tracked_stat_scan": False}
    data = index_path.read_bytes()
    if len(data) < 12 or data[:4] != b"DIRC":
        raise RuntimeError("Índice Git inválido.")
    version_value, count = struct.unpack(">II", data[4:12])
    if version_value not in {2, 3}:
        return {"index_version": version_value, "index_entries": count, "possibly_modified": [], "missing": [], "tracked_stat_scan": False}
    offset = 12
    rows: list[tuple[str, int, int]] = []
    for _ in range(count):
        entry_start = offset
        if offset + 62 > len(data):
            raise RuntimeError("Índice Git truncado.")
        fields = struct.unpack(">10I", data[offset : offset + 40])
        offset += 60
        flags = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 4 if flags & 0x4000 else 2
        name_end = data.find(b"\0", offset)
        if name_end < 0:
            raise RuntimeError("Entrada inválida no índice Git.")
        name = data[offset:name_end].decode("utf-8", errors="surrogateescape")
        rows.append((name, fields[2], fields[9]))
        offset = entry_start + (((name_end + 1 - entry_start) + 7) // 8) * 8
    changed: list[str] = []
    missing: list[str] = []
    for name, mtime_seconds, size in rows:
        try:
            stat = (root / name).stat()
        except OSError:
            missing.append(name)
            continue
        if stat.st_size != size or int(stat.st_mtime) != mtime_seconds:
            changed.append(name)
    return {"index_version": version_value, "index_entries": count, "possibly_modified": changed[:_MAX_ITEMS], "missing": missing[:_MAX_ITEMS], "tracked_stat_scan": True}


def _git_status(root: Path) -> dict[str, object]:
    git_dir = _resolve_git_dir(root)
    branch, head = _read_git_head(git_dir)
    return {"root": str(root), "branch": branch, "head": head, **_read_git_index(root, git_dir), "untracked_scanned": False, "status_method": "local_git_metadata", "note": "Resumo por metadados locais; arquivos não rastreados não são varridos."}


def execute_query(operation: str, target: str | None = None, limit: int = 50) -> object:
    """Execute one allowlisted read-only operation without a command shell."""
    limit = max(1, min(int(limit), _MAX_ITEMS))
    root = _project_root()
    operation = operation.casefold().strip()

    if operation == "date_time":
        return {"local_time": datetime.now().astimezone().isoformat()}
    if operation == "list_files":
        path = _safe_project_path(target)
        if not path.is_dir():
            raise ValueError("O destino não é uma pasta.")
        return [
            {"name": item.name, "type": "directory" if item.is_dir() else "file", "size": item.stat().st_size}
            for item in sorted(path.iterdir(), key=lambda item: item.name.casefold())[:limit]
            if not _contains_blocked_part(item.relative_to(root))
        ]
    if operation == "read_file":
        path = _safe_project_path(target)
        if not path.is_file():
            raise ValueError("O destino não é um arquivo comum.")
        return {"path": str(path.relative_to(root)), "text": _tail_text(path, limit)}
    if operation == "processes":
        rows: list[dict[str, object]] = []
        for process in psutil.process_iter(["pid", "ppid", "name", "create_time"]):
            try:
                info = process.info
                rows.append(
                    {
                        "pid": info["pid"],
                        "parent_pid": info["ppid"],
                        "name": info["name"],
                        "created_at": datetime.fromtimestamp(info["create_time"]).astimezone().isoformat()
                        if info.get("create_time")
                        else None,
                    }
                )
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        if target:
            rows = [row for row in rows if target.casefold() in str(row["name"]).casefold()]
        return rows[:limit]
    if operation == "service":
        if not target or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", target):
            raise ValueError("Informe o nome exato de um serviço.")
        service = psutil.win_service_get(target).as_dict()
        return {key: service.get(key) for key in ("name", "display_name", "status", "start_type")}
    if operation == "git_status":
        return _git_status(root)
    if operation == "program_version":
        return _program_version(target or "", root)
    if operation == "tunnel_status":
        return _tunnel_status(root)
    if operation == "log_tail":
        path = _safe_project_path(target)
        if path.suffix.casefold() not in {".log", ".jsonl", ".txt"}:
            raise ValueError("Somente arquivos de log ou texto podem ser consultados nesta operação.")
        return {"path": str(path.relative_to(root)), "text": _tail_text(path, limit)}
    raise ValueError(
        "Operação não permitida. Use date_time, list_files, read_file, processes, service, "
        "git_status, program_version, tunnel_status ou log_tail."
    )


def register(
    mcp: Any,
    *,
    get_desktop: Callable[[], Any],
    get_analytics: Callable[[], Any],
) -> None:
    @mcp.tool(
        name="SystemQuery",
        description=(
            "Structured read-only Windows and project query. Use instead of PowerShell for date/time, "
            "file listing, ordinary text-file reads, process or service status, Git status, program "
            "versions, tunnel/runtime status, and log tails. It does not accept arbitrary commands, "
            "does not write, blocks secret-like paths, and keeps all file access inside the project."
        ),
        annotations=ToolAnnotations(
            title="Safe System Query",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "SystemQuery-Tool")
    def system_query_tool(
        operation: str,
        target: str | None = None,
        limit: int = 50,
        ctx: Context = None,
    ) -> object:
        return execute_query(operation, target, limit)
