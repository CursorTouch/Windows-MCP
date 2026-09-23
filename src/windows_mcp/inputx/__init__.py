"""Self-contained Windows keyboard and human-like input primitives."""

from windows_mcp.inputx.service import HumanInputService, KEY_NAME_TABLE, normalize_key_name

__all__ = ["HumanInputService", "KEY_NAME_TABLE", "normalize_key_name"]
