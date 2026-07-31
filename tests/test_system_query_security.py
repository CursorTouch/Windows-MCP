import sys
import time
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from windows_mcp.tools.safety_dry_run import evaluate_probe
from windows_mcp.tools import system_query
from windows_mcp.tools.system_query import QueryTimeoutError, execute_query


def test_safe_read_operations_stay_inside_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINDOWS_MCP_PROJECT_ROOT", str(tmp_path))
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text("line one\nline two\n", encoding="utf-8")

    names = {row["name"] for row in execute_query("list_files")}
    content = execute_query("read_file", "ordinary.txt", 10)

    assert "ordinary.txt" in names
    assert content["text"] == "line one\nline two"
    assert execute_query("date_time")["local_time"]


def test_read_blocks_escape_and_secret_like_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINDOWS_MCP_PROJECT_ROOT", str(tmp_path))
    (tmp_path / ".env").write_text("TOKEN=do-not-read", encoding="utf-8")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError, match="protegido"):
        execute_query("read_file", ".env")
    with pytest.raises(ValueError, match="dentro da pasta"):
        execute_query("read_file", str(outside))


def test_arbitrary_commands_are_not_an_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINDOWS_MCP_PROJECT_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="Operação não permitida"):
        execute_query("Remove-Item C:\\")


def test_dry_run_allows_safe_probe_without_execution() -> None:
    result = evaluate_probe("date_time")

    assert result == {
        "decision": "allow",
        "probe": "date_time",
        "read_only": True,
        "executed": False,
        "reason": "Consulta de data e hora; nenhuma escrita.",
    }


@pytest.mark.parametrize(
    "probe",
    [
        "broad_delete",
        "disk_format",
        "credential_change",
        "secret_read",
        "shutdown",
        "outside_scope_write",
        "broad_path_write",
        "environment_dump",
    ],
)
def test_dry_run_denies_dangerous_probe_per_call(probe: str) -> None:
    with pytest.raises(ToolError, match="SOMENTE NESTA CHAMADA"):
        evaluate_probe(probe)


def test_allowlisted_command_timeout_is_bounded_and_isolated() -> None:
    started = time.perf_counter()
    with pytest.raises(QueryTimeoutError, match="sessão MCP permanece ativa"):
        system_query._completed(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=1,
        )
    assert time.perf_counter() - started < 5


def test_git_status_uses_only_local_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git_dir = tmp_path / ".git"
    ref = git_dir / "refs" / "heads" / "codex"
    ref.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/codex/test\n", encoding="ascii")
    commit = "1" * 40
    (ref / "test").write_text(commit + "\n", encoding="ascii")
    (git_dir / "index").write_bytes(b"DIRC" + (2).to_bytes(4, "big") + (0).to_bytes(4, "big"))

    def forbidden_completed(*args: object, **kwargs: object) -> str:
        raise AssertionError("git_status must not start an external process")

    monkeypatch.setenv("WINDOWS_MCP_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(system_query, "_completed", forbidden_completed)
    result = execute_query("git_status")

    assert result["branch"] == "codex/test"
    assert result["head"] == commit
    assert result["status_method"] == "local_git_metadata"
    assert result["tracked_stat_scan"] is True
    assert result["untracked_scanned"] is False
