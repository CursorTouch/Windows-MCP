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
    # When True (and transport is streamable-http) FastMCP is run with
    # stateless_http=True: no Mcp-Session-Id header is issued and the
    # server does not maintain per-connection session state. Useful for
    # resilience against server restarts (a reconnecting client is not
    # rejected with "Session not found") and for horizontal scaling
    # behind a load balancer.
    stateless_http: bool = False


@dataclass
class SecurityConfig:
    ip_allowlist: list[str] = field(default_factory=list)
    cors_origins: list[str] = field(default_factory=list)
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None


@dataclass
class ToolsConfig:
    exclude: list[str] = field(default_factory=list)


@dataclass
class WindowsMCPConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
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


def _strict_string(raw: object, key: str) -> str:
    if isinstance(raw, str):
        return raw
    raise ValueError(f"{key} must be a TOML string, not {type(raw).__name__}")


def _strict_int(raw: object, key: str) -> int:
    # TOML booleans are a subclass of int; reject them explicitly.
    if isinstance(raw, bool):
        raise ValueError(f"{key} must be a TOML integer, not bool")
    if isinstance(raw, int):
        return raw
    raise ValueError(f"{key} must be a TOML integer, not {type(raw).__name__}")


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
        t = _strict_string(server["transport"], "server.transport")
        if t not in _VALID_TRANSPORTS:
            raise ValueError(f"server.transport must be one of {_VALID_TRANSPORTS}, got {t!r}")
        cfg.server.transport = t
    if "host" in server:
        cfg.server.host = _strict_string(server["host"], "server.host")
    if "port" in server:
        cfg.server.port = _strict_int(server["port"], "server.port")
        if not 0 <= cfg.server.port <= 65535:
            raise ValueError("server.port must be between 0 and 65535")
    if "allow_insecure_remote" in server:
        cfg.server.allow_insecure_remote = _strict_bool(
            server["allow_insecure_remote"], "server.allow_insecure_remote"
        )
    if "auth_key" in server:
        cfg.server.auth_key = _strict_string(server["auth_key"], "server.auth_key") or None
    if "ssl_certfile" in server:
        raw = _strict_string(server["ssl_certfile"], "server.ssl_certfile") or None
        if raw:
            cfg.server.ssl_certfile = str((path.parent / raw).resolve())
    if "ssl_keyfile" in server:
        raw = _strict_string(server["ssl_keyfile"], "server.ssl_keyfile") or None
        if raw:
            cfg.server.ssl_keyfile = str((path.parent / raw).resolve())
    if "stateless_http" in server:
        cfg.server.stateless_http = _strict_bool(server["stateless_http"], "server.stateless_http")

    if "ip_allowlist" in security:
        cfg.security.ip_allowlist = _list_of_strings(
            security["ip_allowlist"], "security.ip_allowlist"
        )
    if "cors_origins" in security:
        cfg.security.cors_origins = _list_of_strings(
            security["cors_origins"], "security.cors_origins"
        )
    if "oauth_client_id" in security:
        cfg.security.oauth_client_id = (
            _strict_string(security["oauth_client_id"], "security.oauth_client_id") or None
        )
    if "oauth_client_secret" in security:
        cfg.security.oauth_client_secret = (
            _strict_string(security["oauth_client_secret"], "security.oauth_client_secret") or None
        )

    if "exclude" in tools:
        cfg.tools.exclude = _list_of_strings(tools["exclude"], "tools.exclude")

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
        server_lines.append(f"port = {sd.port}")
    if sd.allow_insecure_remote:
        server_lines.append("allow_insecure_remote = true")
    if sd.auth_key:
        server_lines.append(f'auth_key = "{sd.auth_key}"')
    if sd.ssl_certfile:
        server_lines.append(f'ssl_certfile = "{sd.ssl_certfile}"')
    if sd.ssl_keyfile:
        server_lines.append(f'ssl_keyfile = "{sd.ssl_keyfile}"')
    if sd.stateless_http:
        server_lines.append("stateless_http = true")
    if server_lines:
        lines += ["[server]"] + server_lines + [""]

    sec = cfg.security
    sec_lines: list[str] = []
    if sec.ip_allowlist:
        items = ", ".join(f'"{ip}"' for ip in sec.ip_allowlist)
        sec_lines.append(f"ip_allowlist = [{items}]")
    if sec.cors_origins:
        items = ", ".join(f'"{o}"' for o in sec.cors_origins)
        sec_lines.append(f"cors_origins = [{items}]")
    if sec.oauth_client_id:
        sec_lines.append(f'oauth_client_id = "{sec.oauth_client_id}"')
    if sec.oauth_client_secret:
        sec_lines.append(f'oauth_client_secret = "{sec.oauth_client_secret}"')
    if sec_lines:
        lines += ["[security]"] + sec_lines + [""]

    if cfg.tools.exclude:
        items = ", ".join(f'"{t}"' for t in cfg.tools.exclude)
        lines += ["[tools]", f"exclude = [{items}]", ""]

    path.write_text("\n".join(lines), encoding="utf-8")
