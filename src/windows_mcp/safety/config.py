"""Configuration for the safety layer (``[safety.*]`` tables in config.toml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_CONFIG_DIR = Path("~/.windows-mcp").expanduser()

DEFAULT_ALLOWED_APPS = ["notepad", "calc", "calculator", "calculatorapp"]
DEFAULT_POWERSHELL_COMMANDS = [
    "Get-ChildItem",
    "Get-Content",
    "Get-Date",
    "Get-Item",
    "Get-Location",
    "Get-Process",
    "Get-Service",
    "Test-Path",
]
DEFAULT_RISKY_SHORTCUTS = [
    "alt+f4",
    "ctrl+w",
    "ctrl+q",
    "ctrl+shift+w",
    "delete",
    "shift+delete",
    "ctrl+d",
]
DEFAULT_INSTALLER_PATTERNS = [
    "msiexec",
    "*.msi",
    "*setup*",
    "*install*",
    "winget",
    "choco",
    "appinstaller",
]
DEFAULT_ELEVATION_PROCESSES = ["consent", "runas"]
DEFAULT_RISKY_CMDLET_VERBS = [
    "Remove",
    "Stop",
    "Uninstall",
    "Install",
    "Clear",
    "Restart",
    "Set",
    "Format",
    "Disable",
]
DEFAULT_REDACT_KEYS = ["text", "content", "value", "command"]


@dataclass
class AppsSafetyConfig:
    enforce: bool = True
    allowed: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_APPS))


@dataclass
class PowerShellSafetyConfig:
    # "disabled": every PowerShell call is refused. "allowlist": one simple command
    # whose cmdlet is listed below. "unrestricted": upstream behaviour (not recommended).
    mode: str = "disabled"
    allowed_commands: list[str] = field(default_factory=lambda: list(DEFAULT_POWERSHELL_COMMANDS))
    max_length: int = 512


@dataclass
class ConfirmSafetyConfig:
    enabled: bool = True
    timeout_seconds: float = 30.0
    risky_shortcuts: list[str] = field(default_factory=lambda: list(DEFAULT_RISKY_SHORTCUTS))
    installer_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_INSTALLER_PATTERNS))
    elevation_processes: list[str] = field(
        default_factory=lambda: list(DEFAULT_ELEVATION_PROCESSES)
    )
    risky_cmdlet_verbs: list[str] = field(default_factory=lambda: list(DEFAULT_RISKY_CMDLET_VERBS))


@dataclass
class AuditSafetyConfig:
    enabled: bool = True
    path: str = str(_CONFIG_DIR / "audit.jsonl")
    screenshot: bool = False
    screenshot_dir: str = str(_CONFIG_DIR / "audit-screenshots")
    redact_keys: list[str] = field(default_factory=lambda: list(DEFAULT_REDACT_KEYS))
    max_value_length: int = 200


@dataclass
class KillSwitchSafetyConfig:
    enabled: bool = True
    flag_file: str = str(_CONFIG_DIR / "KILL")
    hotkey: str = "ctrl+alt+shift+k"


@dataclass
class SafetyConfig:
    enabled: bool = True
    apps: AppsSafetyConfig = field(default_factory=AppsSafetyConfig)
    powershell: PowerShellSafetyConfig = field(default_factory=PowerShellSafetyConfig)
    confirm: ConfirmSafetyConfig = field(default_factory=ConfirmSafetyConfig)
    audit: AuditSafetyConfig = field(default_factory=AuditSafetyConfig)
    kill_switch: KillSwitchSafetyConfig = field(default_factory=KillSwitchSafetyConfig)


_POWERSHELL_MODES = ("disabled", "allowlist", "unrestricted")


def _bool(raw: object, key: str) -> bool:
    if isinstance(raw, bool):
        return raw
    raise ValueError(f"{key} must be a TOML boolean, not {type(raw).__name__}")


def _str(raw: object, key: str) -> str:
    if isinstance(raw, str):
        return raw
    raise ValueError(f"{key} must be a TOML string, not {type(raw).__name__}")


def _strs(raw: object, key: str) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(i, str) for i in raw):
        raise ValueError(f"{key} must be an array of strings")
    return list(raw)


def _positive(raw: object, key: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not raw > 0:
        raise ValueError(f"{key} must be a positive number")
    return float(raw)


def _table(data: dict, key: str) -> dict:
    raw = data.get(key, {})
    if not isinstance(raw, dict):
        raise ValueError(f"safety.{key} must be a table")
    return raw


def _check_keys(table: dict, allowed: set[str], prefix: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        # Fail loudly: a typo in a safety key must not silently keep a weaker default.
        raise ValueError(f"Unknown {prefix} keys: {', '.join(sorted(unknown))}")


def parse_safety_config(data: object) -> SafetyConfig:
    """Validate the ``[safety]`` TOML table and return a :class:`SafetyConfig`.

    Args:
        data: The parsed ``safety`` table (a dict), or None for defaults.

    Raises:
        ValueError: On wrong types, unknown keys, or invalid values.
    """
    cfg = SafetyConfig()
    if data is None:
        return cfg
    if not isinstance(data, dict):
        raise ValueError("safety must be a table")
    _check_keys(
        data, {"enabled", "apps", "powershell", "confirm", "audit", "kill_switch"}, "safety"
    )
    if "enabled" in data:
        cfg.enabled = _bool(data["enabled"], "safety.enabled")

    apps = _table(data, "apps")
    _check_keys(apps, {"enforce", "allowed"}, "safety.apps")
    if "enforce" in apps:
        cfg.apps.enforce = _bool(apps["enforce"], "safety.apps.enforce")
    if "allowed" in apps:
        cfg.apps.allowed = _strs(apps["allowed"], "safety.apps.allowed")

    ps = _table(data, "powershell")
    _check_keys(ps, {"mode", "allowed_commands", "max_length"}, "safety.powershell")
    if "mode" in ps:
        mode = _str(ps["mode"], "safety.powershell.mode")
        if mode not in _POWERSHELL_MODES:
            raise ValueError(f"safety.powershell.mode must be one of {_POWERSHELL_MODES}")
        cfg.powershell.mode = mode
    if "allowed_commands" in ps:
        cfg.powershell.allowed_commands = _strs(
            ps["allowed_commands"], "safety.powershell.allowed_commands"
        )
    if "max_length" in ps:
        cfg.powershell.max_length = int(_positive(ps["max_length"], "safety.powershell.max_length"))

    confirm = _table(data, "confirm")
    list_keys = (
        "risky_shortcuts",
        "installer_patterns",
        "elevation_processes",
        "risky_cmdlet_verbs",
    )
    _check_keys(confirm, {"enabled", "timeout_seconds", *list_keys}, "safety.confirm")
    if "enabled" in confirm:
        cfg.confirm.enabled = _bool(confirm["enabled"], "safety.confirm.enabled")
    if "timeout_seconds" in confirm:
        cfg.confirm.timeout_seconds = _positive(
            confirm["timeout_seconds"], "safety.confirm.timeout_seconds"
        )
    for key in list_keys:
        if key in confirm:
            setattr(cfg.confirm, key, _strs(confirm[key], f"safety.confirm.{key}"))

    audit = _table(data, "audit")
    _check_keys(
        audit,
        {"enabled", "path", "screenshot", "screenshot_dir", "redact_keys", "max_value_length"},
        "safety.audit",
    )
    for key in ("enabled", "screenshot"):
        if key in audit:
            setattr(cfg.audit, key, _bool(audit[key], f"safety.audit.{key}"))
    for key in ("path", "screenshot_dir"):
        if key in audit:
            setattr(cfg.audit, key, str(Path(_str(audit[key], f"safety.audit.{key}")).expanduser()))
    if "redact_keys" in audit:
        cfg.audit.redact_keys = _strs(audit["redact_keys"], "safety.audit.redact_keys")
    if "max_value_length" in audit:
        cfg.audit.max_value_length = int(
            _positive(audit["max_value_length"], "safety.audit.max_value_length")
        )

    ks = _table(data, "kill_switch")
    _check_keys(ks, {"enabled", "flag_file", "hotkey"}, "safety.kill_switch")
    if "enabled" in ks:
        cfg.kill_switch.enabled = _bool(ks["enabled"], "safety.kill_switch.enabled")
    if "flag_file" in ks:
        cfg.kill_switch.flag_file = str(
            Path(_str(ks["flag_file"], "safety.kill_switch.flag_file")).expanduser()
        )
    if "hotkey" in ks:
        cfg.kill_switch.hotkey = _str(ks["hotkey"], "safety.kill_switch.hotkey")
    return cfg
