"""Infrastructure layer — cross-cutting concerns: auth, security, analytics, config, oauth."""

from windows_mcp.infrastructure.auth import AuthKeyMiddleware, OAuthOnlyMiddleware, is_loopback_host
from windows_mcp.infrastructure.security import (
    IPAllowlistMiddleware,
    parse_ip_allowlist,
    safe_get,
    validate_url,
)
from windows_mcp.infrastructure.thread_affinity import (
    is_uia_thread,
    run_uia_affine,
    uia_thread_id,
)
from windows_mcp.infrastructure.analytics import Analytics, PostHogAnalytics, with_analytics
from windows_mcp.infrastructure.config import (
    WindowsMCPConfig,
    ServerConfig,
    SecurityConfig,
    ToolsConfig,
    CONFIG_DIR,
    CONFIG_FILE,
    discover_config_path,
    load_config,
    write_config,
)
from windows_mcp.infrastructure.oauth import OAuthStore, build_oauth_routes, validate_oauth_token
from windows_mcp.infrastructure.eventloop import install_selfpipe_guard

__all__ = [
    "AuthKeyMiddleware",
    "OAuthOnlyMiddleware",
    "is_loopback_host",
    "IPAllowlistMiddleware",
    "parse_ip_allowlist",
    "safe_get",
    "validate_url",
    "is_uia_thread",
    "run_uia_affine",
    "uia_thread_id",
    "Analytics",
    "PostHogAnalytics",
    "with_analytics",
    "WindowsMCPConfig",
    "ServerConfig",
    "SecurityConfig",
    "ToolsConfig",
    "CONFIG_DIR",
    "CONFIG_FILE",
    "discover_config_path",
    "load_config",
    "write_config",
    "OAuthStore",
    "build_oauth_routes",
    "validate_oauth_token",
    "install_selfpipe_guard",
]
