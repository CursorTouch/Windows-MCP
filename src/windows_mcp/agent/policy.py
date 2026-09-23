"""Policy abstraction, registry, and factory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from windows_mcp.agent.config import AgentConfig, load_agent_config


@dataclass(slots=True)
class Decision:
    """A validated operation choice produced by a policy."""

    choice: str
    operation: str
    target: str | None
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)
    operation_probabilities: dict[str, float] = field(default_factory=dict)
    target_probabilities: dict[str, float] = field(default_factory=dict)
    target_confidence: float | None = None
    raw_answers: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    request: dict[str, Any] | None = None
    #: True when the service replaced the model's target with the only candidate
    #: for that operation. Surfaced for transparency: the model did not pick it.
    resolved_deterministically: bool = False


class Policy(Protocol):
    """Pluggable browser-decision policy."""

    name: str

    @property
    def available(self) -> bool:
        """Return whether credentials and dependencies are ready."""

        ...

    def decide(
        self, state: dict[str, Any], goal: str, history: list[dict[str, Any]]
    ) -> Decision:
        """Choose one typed operation for the observed state."""

        ...

    def text(self, context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Generate one field value for a TYPE_TEXT action."""

        ...


PolicyFactory = Callable[[AgentConfig], Policy]
_FACTORIES: dict[str, PolicyFactory] = {}


class PolicyUnavailableError(RuntimeError):
    """Raised when the requested policy has no usable backend."""


def register_policy(name: str, factory: PolicyFactory) -> None:
    """Register a policy factory under a case-insensitive name."""

    normalized = name.strip().casefold()
    if not normalized:
        raise ValueError("Policy name must not be empty.")
    _FACTORIES[normalized] = factory


def _construct(name: str, config: AgentConfig) -> Policy:
    factory = _FACTORIES.get(name)
    if factory is None:
        raise ValueError(
            f"Unknown policy {name!r}. Expected auto, jev, openrouter, or laya."
        )
    return factory(config)


def _register_builtin_policies() -> None:
    if all(name in _FACTORIES for name in ("jev", "openrouter", "laya")):
        return
    from windows_mcp.agent.jev import JevPolicy
    from windows_mcp.agent.laya import LayaPolicy
    from windows_mcp.agent.openrouter import OpenRouterPolicy

    register_policy("jev", JevPolicy)
    register_policy("openrouter", OpenRouterPolicy)
    register_policy("laya", LayaPolicy)


def get_policy(name: str = "auto", config: AgentConfig | None = None) -> Policy:
    """Return an available policy by name.

    ``auto`` prefers TypeSafe Jev, then OpenRouter, then the Laya stub. If no
    backend is ready, the error explains which environment variables or
    ``[agent]`` config keys to set.
    """

    _register_builtin_policies()
    resolved_config = config or load_agent_config()
    normalized = (name or "auto").strip().casefold()
    if normalized == "auto":
        for candidate in ("jev", "openrouter", "laya"):
            policy = _construct(candidate, resolved_config)
            if policy.available:
                return policy
        raise PolicyUnavailableError(
            "No browser-agent policy is available. Configure TypeSafe with "
            "WINDOWS_MCP_TYPESAFE_API_KEY or [agent].typesafe_api_key; configure "
            "OpenRouter with WINDOWS_MCP_OPENROUTER_API_KEY or "
            "[agent].openrouter_api_key; or set [agent].laya_onnx_dir plus Node.js "
            "for the reserved Laya backend."
        )
    if normalized not in _FACTORIES:
        raise ValueError(
            f"Unknown policy {name!r}. Expected auto, jev, openrouter, or laya."
        )
    policy = _construct(normalized, resolved_config)
    if not policy.available:
        raise PolicyUnavailableError(
            f"Policy {normalized!r} is unavailable. Check its API key or dependency "
            "configuration in environment variables or [agent] in "
            "~/.windows-mcp/config.toml."
        )
    return policy


__all__ = [
    "Decision",
    "Policy",
    "PolicyUnavailableError",
    "get_policy",
    "register_policy",
]
