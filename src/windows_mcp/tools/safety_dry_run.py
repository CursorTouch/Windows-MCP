"""Non-destructive security rejection probe."""

from collections.abc import Callable
from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from windows_mcp.infrastructure import with_analytics

_SAFE_PROBES = {
    "date_time": "Consulta de data e hora; nenhuma escrita.",
    "file_list": "Listagem de nomes e metadados dentro do projeto; nenhuma escrita.",
    "process_list": "Consulta de processos; nenhuma escrita.",
}
_DENIED_PROBES = {
    "broad_delete": "Exclusão ampla e de difícil reversão.",
    "disk_format": "Formatação de disco é destrutiva.",
    "credential_change": "Alteração de credenciais exige autorização humana específica.",
    "secret_read": "Leitura ou exposição de segredos é proibida.",
    "shutdown": "Desligamento do computador não foi autorizado.",
    "outside_scope_write": "Escrita fora da pasta autorizada.",
    "broad_path_write": "Ação de escrita com caminho amplo e alcance inseguro.",
    "environment_dump": "Exposição ampla de variáveis pode revelar credenciais.",
}


def evaluate_probe(probe: str) -> dict[str, object]:
    """Classify a named probe without parsing or executing a command."""
    normalized = probe.casefold().strip()
    if normalized in _SAFE_PROBES:
        return {
            "decision": "allow",
            "probe": normalized,
            "read_only": True,
            "executed": False,
            "reason": _SAFE_PROBES[normalized],
        }
    if normalized in _DENIED_PROBES:
        raise ToolError(
            f"AÇÃO BLOQUEADA SOMENTE NESTA CHAMADA: {_DENIED_PROBES[normalized]} "
            "Nada foi executado; a sessão MCP deve permanecer ativa."
        )
    raise ToolError(
        "AÇÃO BLOQUEADA SOMENTE NESTA CHAMADA: cenário de teste desconhecido. "
        "Nada foi executado; a sessão MCP deve permanecer ativa."
    )


def register(
    mcp: Any,
    *,
    get_desktop: Callable[[], Any],
    get_analytics: Callable[[], Any],
) -> None:
    @mcp.tool(
        name="SafetyDryRun",
        description=(
            "Evaluate a named security scenario without executing any operating-system action. "
            "Safe probes return an allow decision. Dangerous probes return a clear per-call denial. "
            "Use this to verify that a rejection does not terminate the MCP session."
        ),
        annotations=ToolAnnotations(
            title="Security Dry Run",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "SafetyDryRun-Tool")
    def safety_dry_run_tool(probe: str, ctx: Context = None) -> dict[str, object]:
        return evaluate_probe(probe)
