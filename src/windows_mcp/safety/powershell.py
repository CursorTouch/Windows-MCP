"""PowerShell command policy: disabled, or a single allowlisted cmdlet without chaining."""

from __future__ import annotations

import re

from windows_mcp.safety.config import PowerShellSafetyConfig

# Anything that can chain, substitute, redirect, escape or open a script block.
_FORBIDDEN_CHARS = frozenset(";|&`$(){}[]<>@\r\n\x00'\"%")
_FORBIDDEN_TOKENS = re.compile(
    r"(?i)(-enc(odedcommand)?\b|-e\s|invoke-expression|\biex\b|-command\b|-c\s|"
    r"start-process|invoke-command|add-type|new-object|::|\.\.|--%)"
)
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*(-[A-Za-z0-9]+)?$")
_ARG = re.compile(r"^-?[A-Za-z0-9_.:\\/*?,=+~-]+$")


def check_powershell(command: str, cfg: PowerShellSafetyConfig) -> str | None:
    """Return None when *command* may run, else the reason it is refused."""
    if cfg.mode == "unrestricted":
        return None
    if cfg.mode == "disabled":
        return (
            'PowerShell is disabled by safety.powershell.mode. Set it to "allowlist" '
            "and list the cmdlets in safety.powershell.allowed_commands."
        )
    if not isinstance(command, str) or not command.strip():
        return "Empty PowerShell command."
    if len(command) > cfg.max_length:
        return f"PowerShell command longer than {cfg.max_length} characters."
    bad = sorted({c for c in command if c in _FORBIDDEN_CHARS})
    if bad:
        shown = " ".join(repr(c) for c in bad)
        return f"PowerShell command contains forbidden characters ({shown}): no chaining, quoting, variables, subexpressions or redirection."
    if _FORBIDDEN_TOKENS.search(command):
        return "PowerShell command contains a forbidden construct (encoded/nested command, Invoke-Expression, Start-Process, ...)."
    tokens = command.split()
    head = tokens[0]
    if not _NAME.match(head):
        return f"Invalid cmdlet name: {head!r}."
    allowed = {c.lower() for c in cfg.allowed_commands}
    if head.lower() not in allowed:
        return f"Cmdlet {head!r} is not in safety.powershell.allowed_commands."
    for arg in tokens[1:]:
        if not _ARG.match(arg):
            return f"Argument {arg!r} contains characters outside the safe set."
    return None


def cmdlet_of(command: str) -> str:
    """Return the first token of *command* (the cmdlet name)."""
    parts = (command or "").split()
    return parts[0] if parts else ""
