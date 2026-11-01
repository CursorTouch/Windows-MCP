"""HTTP transport exposure policy: loopback-only bind and mandatory bearer token."""

from __future__ import annotations


def check_http_exposure(
    *,
    transport: str,
    host: str,
    auth_key: str | None,
    oauth_configured: bool,
    require_auth: bool,
    allow_remote_bind: bool,
    is_loopback,
) -> str | None:
    """Return an error message when the HTTP server must not start, else None.

    Args:
        transport: ``stdio``, ``sse`` or ``streamable-http``.
        host: Bind address.
        auth_key: Static bearer token, if any.
        oauth_configured: Whether OAuth client credentials are configured.
        require_auth: ``server.require_auth`` (default True).
        allow_remote_bind: ``server.allow_remote_bind`` (default False).
        is_loopback: Callable deciding whether *host* is loopback-only.
    """
    if transport == "stdio":
        return None
    if not is_loopback(host) and not allow_remote_bind:
        return (
            f"Refusing to bind HTTP transport to '{host}': only localhost/127.0.0.1/::1 are "
            "allowed by default. Set server.allow_remote_bind = true to override."
        )
    if require_auth and not auth_key and not oauth_configured:
        return (
            "Refusing to start HTTP transport without a bearer token (server.require_auth = "
            "true). Run `windows-mcp auth` or pass --auth-key <token>."
        )
    return None
