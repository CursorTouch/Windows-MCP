"""Safety layer: app allowlist, PowerShell policy, confirmations, audit log, kill switch.

Every tool call passes through :class:`SafetyGate` (a FastMCP middleware) before any
other middleware or tool code runs. All protections are configured from the
``[safety.*]`` tables of ``config.toml`` (see ``docs/safety.md``) and are enabled by
default. Windows-specific calls live in :mod:`windows_mcp.safety.win32` and are
injected, so the policy code is importable and testable on any OS.
"""

from windows_mcp.safety.config import SafetyConfig, parse_safety_config
from windows_mcp.safety.gate import SafetyGate, build_safety_gate

__all__ = ["SafetyConfig", "SafetyGate", "build_safety_gate", "parse_safety_config"]
