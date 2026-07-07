"""Configuration loading and merge utilities for windows-mcp."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ServerConfig:
    transport: str = "stdio"
    host: str = "localhost"
    port: int = 8000
    allow_insecure_remote: bool = False
    auth_key: str | None = None
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None


@dataclass
class SecurityConfig:
    ip_allowlist: list[str] = field(default_factory=list)
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None


@dataclass
class ToolsConfig:
    exclude: list[str] = field(default_factory=list)


SECURE_DESKTOP_POLICIES = ("block", "allow_with_match", "allow_all")


@dataclass
class SecureDesktopConfig:
    """Policy for how the LocalSystem host service may handle UAC consent prompts.

    ``policy`` values:
      - ``block``           — service exposes the dialog to the agent but
                              REFUSES to auto-click Yes/No. Human approval
                              still required. (default)
      - ``allow_with_match``— auto-click only if the requesting binary's
                              publisher (CommonName from the Authenticode
                              signature) matches one of ``publishers_allowlist``.
      - ``allow_all``       — auto-click any UAC prompt. Only safe in sandboxed
                              VMs. Opt-in.
    """
    policy: str = "block"
    publishers_allowlist: list[str] = field(default_factory=list)


@dataclass
class WindowsMCPConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    secure_desktop: SecureDesktopConfig = field(default_factory=SecureDesktopConfig)
    source_path: Path | None = None


CONFIG_DIR = Path("~/.windows-mcp").expanduser()
CONFIG_FILE = CONFIG_DIR / "config.toml"


def discover_config_path(explicit_path: str | None) -> Path | None:
    """Find config path using precedence: explicit > ~/.windows-mcp/config.toml."""
    if explicit_path:
        return Path(explicit_path).expanduser()

    if CONFIG_FILE.exists():
        return CONFIG_FILE

    return None


def _list_of_strings(raw: object, key: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(i, str) for i in raw):
        raise ValueError(f"{key} must be an array of strings")
    return raw


def _strict_bool(raw: object, key: str) -> bool:
    if isinstance(raw, bool):
        return raw
    raise ValueError(f"{key} must be a TOML boolean, not {type(raw).__name__}")


def load_config(path: Path | None) -> WindowsMCPConfig:
    """Load and validate TOML config file. Returns defaults when path is None."""
    cfg = WindowsMCPConfig()
    if path is None:
        return cfg

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    data = tomllib.loads(path.read_text(encoding="utf-8"))

    server = data.get("server", {})
    security = data.get("security", {})
    tools = data.get("tools", {})

    _VALID_TRANSPORTS = {"stdio", "sse", "streamable-http"}
    if "transport" in server:
        t = str(server["transport"])
        if t not in _VALID_TRANSPORTS:
            raise ValueError(f"server.transport must be one of {_VALID_TRANSPORTS}, got {t!r}")
        cfg.server.transport = t
    if "host" in server:
        cfg.server.host = str(server["host"])
    if "port" in server:
        cfg.server.port = int(server["port"])
    if "allow_insecure_remote" in server:
        cfg.server.allow_insecure_remote = _strict_bool(
            server["allow_insecure_remote"], "server.allow_insecure_remote"
        )
    if "auth_key" in server:
        cfg.server.auth_key = str(server["auth_key"]) or None
    if "ssl_certfile" in server:
        raw = str(server["ssl_certfile"]) or None
        if raw:
            cfg.server.ssl_certfile = str((path.parent / raw).resolve())
    if "ssl_keyfile" in server:
        raw = str(server["ssl_keyfile"]) or None
        if raw:
            cfg.server.ssl_keyfile = str((path.parent / raw).resolve())

    if "ip_allowlist" in security:
        cfg.security.ip_allowlist = _list_of_strings(security["ip_allowlist"], "security.ip_allowlist")
    if "oauth_client_id" in security:
        cfg.security.oauth_client_id = str(security["oauth_client_id"]) or None
    if "oauth_client_secret" in security:
        cfg.security.oauth_client_secret = str(security["oauth_client_secret"]) or None

    if "exclude" in tools:
        cfg.tools.exclude = _list_of_strings(tools["exclude"], "tools.exclude")

    secure_desktop = data.get("secure_desktop", {})
    if "policy" in secure_desktop:
        p = str(secure_desktop["policy"])
        if p not in SECURE_DESKTOP_POLICIES:
            raise ValueError(
                f"secure_desktop.policy must be one of {SECURE_DESKTOP_POLICIES}, got {p!r}"
            )
        cfg.secure_desktop.policy = p
    if "publishers_allowlist" in secure_desktop:
        cfg.secure_desktop.publishers_allowlist = _list_of_strings(
            secure_desktop["publishers_allowlist"], "secure_desktop.publishers_allowlist"
        )

    cfg.source_path = path
    return cfg


def write_config(cfg: WindowsMCPConfig, path: Path) -> None:
    """Serialize *cfg* to TOML at *path*, writing only non-default values."""
    lines: list[str] = []

    sd, dd = cfg.server, ServerConfig()
    server_lines: list[str] = []
    if sd.transport != dd.transport:
        server_lines.append(f'transport = "{sd.transport}"')
    if sd.host != dd.host:
        server_lines.append(f'host = "{sd.host}"')
    if sd.port != dd.port:
        server_lines.append(f'port = {sd.port}')
    if sd.allow_insecure_remote:
        server_lines.append('allow_insecure_remote = true')
    if sd.auth_key:
        server_lines.append(f'auth_key = "{sd.auth_key}"')
    if sd.ssl_certfile:
        server_lines.append(f'ssl_certfile = "{sd.ssl_certfile}"')
    if sd.ssl_keyfile:
        server_lines.append(f'ssl_keyfile = "{sd.ssl_keyfile}"')
    if server_lines:
        lines += ['[server]'] + server_lines + ['']

    sec = cfg.security
    sec_lines: list[str] = []
    if sec.ip_allowlist:
        items = ', '.join(f'"{ip}"' for ip in sec.ip_allowlist)
        sec_lines.append(f'ip_allowlist = [{items}]')
    if sec.oauth_client_id:
        sec_lines.append(f'oauth_client_id = "{sec.oauth_client_id}"')
    if sec.oauth_client_secret:
        sec_lines.append(f'oauth_client_secret = "{sec.oauth_client_secret}"')
    if sec_lines:
        lines += ['[security]'] + sec_lines + ['']

    if cfg.tools.exclude:
        items = ', '.join(f'"{t}"' for t in cfg.tools.exclude)
        lines += ['[tools]', f'exclude = [{items}]', '']

    sd_cfg, sd_def = cfg.secure_desktop, SecureDesktopConfig()
    sd_lines: list[str] = []
    if sd_cfg.policy != sd_def.policy:
        sd_lines.append(f'policy = "{sd_cfg.policy}"')
    if sd_cfg.publishers_allowlist:
        items = ', '.join(f'"{p}"' for p in sd_cfg.publishers_allowlist)
        sd_lines.append(f'publishers_allowlist = [{items}]')
    if sd_lines:
        lines += ['[secure_desktop]'] + sd_lines + ['']

    path.write_text('\n'.join(lines), encoding='utf-8')
