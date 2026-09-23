"""Pluggable policy configuration for the browser agent."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from windows_mcp.infrastructure.config import discover_config_path

DEFAULT_TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "inception/mercury-2.5"
DEFAULT_TYPESAFE_MODEL = "jev-latest"
#: Jev is a *decisions* model, not a chat model, so on OpenRouter it lives behind
#: a separate endpoint and is absent from the /models listing. The same model is
#: reachable with an OpenRouter key alone.
DEFAULT_OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_OPENROUTER_DECISIONS_MODEL = "typesafe/jev-1.13"


@dataclass(slots=True)
class AgentConfig:
    """Resolved policy credentials and model settings.

    Secret values are excluded from ``repr`` so accidental diagnostic logging
    cannot disclose them.
    """

    typesafe_api_key: str | None = field(default=None, repr=False)
    typesafe_model: str = DEFAULT_TYPESAFE_MODEL
    typesafe_base_url: str = DEFAULT_TYPESAFE_URL
    openrouter_api_key: str | None = field(default=None, repr=False)
    openrouter_base_url: str = DEFAULT_OPENROUTER_BASE_URL
    openrouter_model: str = DEFAULT_OPENROUTER_MODEL
    text_model_api_key: str | None = field(default=None, repr=False)
    text_model_base_url: str = DEFAULT_OPENROUTER_BASE_URL
    text_model: str = DEFAULT_OPENROUTER_MODEL
    openrouter_decisions_url: str = DEFAULT_OPENROUTER_DECISIONS_URL
    openrouter_decisions_model: str = DEFAULT_OPENROUTER_DECISIONS_MODEL
    laya_onnx_dir: str | None = None
    laya_repo: str = "receptron/laya-onnx"


def _env_first(names: tuple[str, ...]) -> str | None:
    """Return the first non-empty environment variable from *names*."""

    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _agent_value(section: dict[str, Any], key: str) -> str | None:
    """Read an optional string from the ``[agent]`` TOML section."""

    if key not in section:
        return None
    value = section[key]
    if not isinstance(value, str):
        raise ValueError(f"agent.{key} must be a TOML string")
    return value.strip() or None


def _load_agent_section(path: str | Path | None = None) -> dict[str, Any]:
    """Load the optional ``[agent]`` table without touching server parsing."""

    explicit = str(Path(path).expanduser()) if path is not None else None
    config_path = discover_config_path(explicit)
    if config_path is None:
        return {}
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Could not read agent config {config_path}: {exc}") from exc
    section = data.get("agent", {})
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ValueError("agent must be a TOML table")
    return section


def load_agent_config(path: str | Path | None = None) -> AgentConfig:
    """Resolve agent settings using environment > config file > defaults.

    The generic environment aliases are accepted for compatibility with the
    upstream Jev examples; the ``WINDOWS_MCP_*`` names take precedence.
    """

    section = _load_agent_section(path)

    typesafe_key = _env_first(("WINDOWS_MCP_TYPESAFE_API_KEY", "TYPESAFE_API_KEY"))
    if typesafe_key is None:
        typesafe_key = _agent_value(section, "typesafe_api_key")
    typesafe_model = _env_first(("WINDOWS_MCP_TYPESAFE_MODEL", "TYPESAFE_MODEL"))
    if typesafe_model is None:
        typesafe_model = _agent_value(section, "typesafe_model") or DEFAULT_TYPESAFE_MODEL
    typesafe_url = _env_first(
        ("WINDOWS_MCP_TYPESAFE_BASE_URL", "TYPESAFE_BASE_URL")
    ) or _agent_value(section, "typesafe_base_url") or DEFAULT_TYPESAFE_URL

    openrouter_key = _env_first(("WINDOWS_MCP_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"))
    if openrouter_key is None:
        openrouter_key = _agent_value(section, "openrouter_api_key")
    openrouter_model = _env_first(("WINDOWS_MCP_OPENROUTER_MODEL", "OPENROUTER_MODEL"))
    if openrouter_model is None:
        openrouter_model = (
            _agent_value(section, "openrouter_model") or DEFAULT_OPENROUTER_MODEL
        )
    openrouter_url = _env_first(("WINDOWS_MCP_OPENROUTER_BASE_URL", "OPENROUTER_BASE_URL"))
    if openrouter_url is None:
        openrouter_url = (
            _agent_value(section, "openrouter_base_url") or DEFAULT_OPENROUTER_BASE_URL
        )

    text_key = _env_first(("WINDOWS_MCP_TEXT_MODEL_API_KEY", "TEXT_MODEL_API_KEY"))
    if text_key is None:
        text_key = _agent_value(section, "text_model_api_key") or openrouter_key
    text_url = _env_first(("WINDOWS_MCP_TEXT_MODEL_BASE_URL", "TEXT_MODEL_BASE_URL"))
    if text_url is None:
        text_url = _agent_value(section, "text_model_base_url") or openrouter_url
    text_model = _env_first(("WINDOWS_MCP_TEXT_MODEL", "TEXT_MODEL"))
    if text_model is None:
        text_model = _agent_value(section, "text_model") or openrouter_model

    laya_dir = _env_first(("WINDOWS_MCP_LAYA_ONNX_DIR", "LAYA_ONNX_DIR"))
    if laya_dir is None:
        laya_dir = _agent_value(section, "laya_onnx_dir")
    laya_repo = _env_first(("WINDOWS_MCP_LAYA_REPO", "LAYA_REPO"))
    if laya_repo is None:
        laya_repo = _agent_value(section, "laya_repo") or "receptron/laya-onnx"

    return AgentConfig(
        typesafe_api_key=typesafe_key,
        typesafe_model=typesafe_model,
        typesafe_base_url=typesafe_url,
        openrouter_api_key=openrouter_key,
        openrouter_base_url=openrouter_url,
        openrouter_model=openrouter_model,
        text_model_api_key=text_key,
        text_model_base_url=text_url,
        text_model=text_model,
        openrouter_decisions_url=(
            _env_first(("WINDOWS_MCP_OPENROUTER_DECISIONS_URL",))
            or _agent_value(section, "openrouter_decisions_url")
            or DEFAULT_OPENROUTER_DECISIONS_URL
        ),
        openrouter_decisions_model=(
            _env_first(("WINDOWS_MCP_OPENROUTER_DECISIONS_MODEL",))
            or _agent_value(section, "openrouter_decisions_model")
            or DEFAULT_OPENROUTER_DECISIONS_MODEL
        ),
        laya_onnx_dir=laya_dir,
        laya_repo=laya_repo,
    )


__all__ = ["AgentConfig", "load_agent_config"]
