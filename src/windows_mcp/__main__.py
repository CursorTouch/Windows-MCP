from contextlib import asynccontextmanager
from windows_mcp.config import enable_debug
from windows_mcp.infrastructure import (
    AuthKeyMiddleware,
    OAuthOnlyMiddleware,
    is_loopback_host,
    IPAllowlistMiddleware,
    parse_ip_allowlist,
    CONFIG_DIR,
    CONFIG_FILE,
    WindowsMCPConfig,
    discover_config_path,
    load_config,
    write_config,
    OAuthStore,
    build_oauth_routes,
    validate_oauth_token,
)
from click.core import ParameterSource
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from textwrap import dedent
from enum import Enum
from typing import Any
import logging
import asyncio
import shlex
import secrets
import subprocess
import click
import os
import sys

logger = logging.getLogger(__name__)


def _echo_section(title: str) -> None:
    """Print a section heading via click.echo, falling back to ASCII
    when the current stdout encoding can't represent the U+2500
    box-drawing character.

    Without this guard, `windows-mcp auth` crashes on the default
    PowerShell / cmd console (cp1252) with a UnicodeEncodeError when
    click.echo hits ─. The config file is already written at this
    point, but the noisy traceback obscures the success and breaks
    scripted callers that treat exit-nonzero as a real failure.
    Setting PYTHONIOENCODING=utf-8 is a workaround; this is the fix.
    """
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "─".encode(enc)
        bar = "─" * 3
    except (UnicodeEncodeError, LookupError):
        bar = "==="
    click.echo(f"\n{bar} {title} {bar}")


desktop: Any | None = None
watchdog: Any | None = None
analytics: Any | None = None
screen_size: Any | None = None
_mcp: FastMCP | None = None

instructions = dedent("""
Windows MCP server provides tools to interact directly with the Windows desktop,
thus enabling to operate the desktop on the user's behalf.
""")


def _get_desktop():
    return desktop


def _get_analytics():
    return analytics


def _http_middleware(
    auth_key: str | None = None,
    ip_allowlist: list | None = None,
    oauth_validator=None,
    cors_origins: list[str] | None = None,
    allowed_hosts: list[str] | None = None,
) -> list:
    """Return ASGI middleware for HTTP transports."""
    middleware: list = [
        Middleware(OptionsMiddleware, allowed_origins=cors_origins or []),
    ]
    if allowed_hosts:
        from starlette.middleware.trustedhost import TrustedHostMiddleware

        middleware.append(Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts))
    if cors_origins:
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=cors_origins,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Content-Type", "Authorization", "Mcp-Session-Id"],
                allow_credentials=False,
            )
        )
    if ip_allowlist:
        middleware.append(Middleware(IPAllowlistMiddleware, allowlist=ip_allowlist))
    if auth_key:
        middleware.append(
            Middleware(AuthKeyMiddleware, auth_key=auth_key, oauth_validator=oauth_validator)
        )
    elif oauth_validator:
        middleware.append(Middleware(OAuthOnlyMiddleware, oauth_validator=oauth_validator))
    return middleware


def _param_explicit(ctx: click.Context, name: str) -> bool:
    src = ctx.get_parameter_source(name)
    return src in {ParameterSource.COMMANDLINE, ParameterSource.ENVIRONMENT}


def _choose_value(ctx: click.Context, name: str, cli_value, config_value, default_value):
    if _param_explicit(ctx, name):
        return cli_value
    if config_value is not None:
        return config_value
    return default_value


class OptionsMiddleware:
    """ASGI middleware that intercepts OPTIONS preflight requests.

    Only echoes CORS headers when the request Origin is in the explicit allowlist.
    With an empty allowlist (default), returns 200 OK with no CORS headers so that
    browsers block cross-origin access via Same-Origin Policy.
    """

    def __init__(self, app: Any, *, allowed_origins: list[str] | None = None) -> None:
        self.app = app
        self._allowed: frozenset[str] = frozenset(allowed_origins or [])

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] == "OPTIONS":
            headers: list[list[bytes]] = [[b"content-length", b"0"]]
            if self._allowed:
                origin = next(
                    (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"origin"),
                    None,
                )
                if origin and origin in self._allowed:
                    headers += [
                        [b"access-control-allow-origin", origin.encode("latin-1")],
                        [b"access-control-allow-methods", b"GET, POST, OPTIONS"],
                        [
                            b"access-control-allow-headers",
                            b"content-type, authorization, mcp-session-id",
                        ],
                        [b"vary", b"Origin"],
                    ]
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
        else:
            await self.app(scope, receive, send)


def _watchdog_enabled() -> bool:
    """Whether the UIA focus WatchDog should run.

    Reads the ``WINDOWS_MCP_WATCHDOG`` environment variable. Disabling values
    (case-insensitive, whitespace-trimmed): ``off``, ``0``, ``false``, ``no``,
    ``disabled``. Anything else, including unset, keeps the watchdog enabled so
    the default behavior is unchanged.

    Returns:
        ``True`` when the watchdog should be started, ``False`` to skip it.
    """
    value = os.getenv("WINDOWS_MCP_WATCHDOG")
    if value is None:
        return True
    return value.strip().lower() not in {"off", "0", "false", "no", "disabled"}


def _build_mcp() -> FastMCP:
    """Create the MCP server instance."""
    global _mcp

    if _mcp is not None:
        return _mcp

    from windows_mcp.infrastructure import PostHogAnalytics
    from windows_mcp.desktop.service import Desktop
    from windows_mcp.tools import register_all

    @asynccontextmanager
    async def lifespan(app: FastMCP):
        """Runs initialization code before the server starts and cleanup code after it shuts down."""
        global desktop, watchdog, analytics, screen_size

        if os.getenv("ANONYMIZED_TELEMETRY", "true").lower() != "false":
            analytics = PostHogAnalytics()
        desktop = Desktop()
        screen_size = desktop.get_screen_size()

        if _watchdog_enabled():
            # Imported lazily so a disabled watchdog never loads comtypes.
            from windows_mcp.watchdog.service import WatchDog

            watchdog = WatchDog()
            watchdog.set_focus_callback(desktop.tree.on_focus_change)
        else:
            watchdog = None
            logger.debug("WatchDog disabled via WINDOWS_MCP_WATCHDOG")

        try:
            if watchdog:
                watchdog.start()
            await asyncio.sleep(1)  # Simulate startup latency
            logger.debug("Server started, entering main loop")
            yield
        finally:
            logger.debug("Shutting down: stopping watchdog and analytics")
            if watchdog:
                watchdog.stop()
            if analytics:
                await analytics.close()

    _mcp = FastMCP(name="windows-mcp", instructions=instructions, lifespan=lifespan)
    register_all(_mcp, get_desktop=_get_desktop, get_analytics=_get_analytics)
    return _mcp


def __getattr__(name: str):
    if name in {"state_tool", "screenshot_tool"}:
        _build_mcp()
        from windows_mcp.tools import snapshot

        tool = getattr(snapshot, name)
        if tool is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        return getattr(tool, "fn", tool)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class Transport(Enum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"

    def __str__(self):
        return self.value


_LEGACY_SERVE_FLAGS = {
    "--transport",
    "--host",
    "--port",
    "--auth-key",
    "--stateless-http",
    "--ip-allowlist",
    "--cors-origins",
    "--ssl-certfile",
    "--ssl-keyfile",
    "--oauth-client-id",
    "--oauth-client-secret",
    "--tools",
    "--exclude-tools",
    "--allow-insecure-remote",
    "--debug",
}


class _LegacyAwareGroup(click.Group):
    """Detect serve flags passed to the top-level command group."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        legacy_arg = self._first_legacy_arg_before_subcommand(args)
        if legacy_arg:
            suggested = shlex.join(["windows-mcp", "serve", *args])
            raise click.UsageError(
                "`windows-mcp` is now a command group. Did you mean:\n"
                f"    {suggested}\n"
                "These flags belong on the `serve` subcommand. "
                "See `windows-mcp serve --help`.",
                ctx=ctx,
            )
        return super().parse_args(ctx, args)

    def _first_legacy_arg_before_subcommand(self, args: list[str]) -> str | None:
        commands = set(self.commands)
        for arg in args:
            if arg in commands:
                return None
            flag = arg.split("=", 1)[0]
            if flag in _LEGACY_SERVE_FLAGS:
                return arg
        return None


def _apply_tool_filter(
    mcp, explicit_tools: list[str] | None, exclude_tools: list[str] | None
) -> None:
    """Remove disabled tools from the MCP registry."""
    tool_mgr = getattr(mcp, "_tool_manager", None)
    tools_dict = getattr(tool_mgr, "_tools", None)
    if tools_dict is None:
        provider = getattr(mcp, "_local_provider", None)
        components = getattr(provider, "_components", {})
        tools_dict = {
            (getattr(v, "name", None) or k.split(":", 1)[1].split("@", 1)[0]): k
            for k, v in components.items()
            if isinstance(k, str) and k.startswith("tool:")
        }

        def _remove(name):
            keys = [
                k
                for k, v in components.items()
                if isinstance(k, str)
                and k.startswith("tool:")
                and (
                    getattr(components[k], "name", None) == name
                    or k.split(":", 1)[1].split("@", 1)[0] == name
                )
            ]
            for k in keys:
                components.pop(k, None)

        registered = set(tools_dict.keys())
    else:

        def _remove(name):
            tools_dict.pop(name, None)

        registered = set(tools_dict.keys())

    if explicit_tools:
        keep = {t for t in explicit_tools if t in registered}
        for name in registered - keep:
            _remove(name)
    elif exclude_tools:
        for name in exclude_tools:
            if name in registered:
                _remove(name)
    logger.debug("Tool filter applied: explicit=%s exclude=%s", explicit_tools, exclude_tools)


def _run_server(
    transport: str,
    host: str,
    port: int,
    auth_key: str | None = None,
    ip_allowlist: list | None = None,
    explicit_tools: list[str] | None = None,
    exclude_tools: list[str] | None = None,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
    oauth_validator=None,
    cors_origins: list[str] | None = None,
    allowed_hosts: list[str] | None = None,
    stateless_http: bool = False,
) -> None:
    mcp = _build_mcp()
    if explicit_tools or exclude_tools:
        _apply_tool_filter(mcp, explicit_tools, exclude_tools)
    match transport:
        case Transport.STDIO.value:
            mcp.run(transport=Transport.STDIO.value, show_banner=False)
        case Transport.SSE.value | Transport.STREAMABLE_HTTP.value:
            uvicorn_config: dict = {}
            if ssl_certfile and ssl_keyfile:
                uvicorn_config["ssl_certfile"] = ssl_certfile
                uvicorn_config["ssl_keyfile"] = ssl_keyfile
            mcp.run(
                transport=transport,
                host=host,
                port=port,
                show_banner=False,
                middleware=_http_middleware(
                    auth_key=auth_key,
                    ip_allowlist=ip_allowlist,
                    oauth_validator=oauth_validator,
                    cors_origins=cors_origins,
                    allowed_hosts=allowed_hosts,
                ),
                uvicorn_config=uvicorn_config or None,
                stateless_http=stateless_http,
            )
        case _:
            raise ValueError(f"Invalid transport: {transport}")


@click.group(cls=_LegacyAwareGroup, no_args_is_help=False)
def main():
    """Windows-MCP: MCP server for Windows desktop automation."""


@main.command()
@click.pass_context
@click.option(
    "--transport",
    help="The transport layer used by the MCP server.",
    type=click.Choice(
        [Transport.STDIO.value, Transport.SSE.value, Transport.STREAMABLE_HTTP.value]
    ),
    default="stdio",
)
@click.option(
    "--host",
    help="Host to bind the SSE/Streamable HTTP server.",
    default="localhost",
    type=str,
    show_default=True,
)
@click.option(
    "--port",
    help="Port to bind the SSE/Streamable HTTP server.",
    default=8000,
    type=int,
    show_default=True,
)
@click.option(
    "--debug",
    help="Enable debug mode to provide verbose logging for troubleshooting.",
    is_flag=True,
    default=False,
    show_default=True,
)
@click.option(
    "--config",
    help="Path to windows-mcp config file (default: ~/.windows-mcp/config.toml).",
    default=None,
    type=click.Path(dir_okay=False),
    show_default=False,
)
@click.option(
    "--auth-key",
    help="Bearer token required on all HTTP requests. Can also be set via WINDOWS_MCP_AUTH_KEY.",
    default=None,
    envvar="WINDOWS_MCP_AUTH_KEY",
    type=str,
    show_default=False,
)
@click.option(
    "--allow-insecure-remote",
    help="Allow binding to non-loopback addresses without authentication (not recommended).",
    is_flag=True,
    default=False,
    show_default=True,
)
@click.option(
    "--ip-allowlist",
    help="Comma-separated list of allowed client IPs or CIDR ranges (e.g. '10.0.0.0/8,192.168.1.5'). IPv4 and IPv6 supported.",
    default=None,
    envvar="WINDOWS_MCP_IP_ALLOWLIST",
    type=str,
    show_default=False,
)
@click.option(
    "--tools",
    help="Comma-separated explicit list of tools to enable (e.g. 'Screenshot,Click,Snapshot'). Overrides --exclude-tools.",
    default=None,
    envvar="WINDOWS_MCP_TOOLS",
    type=str,
    show_default=False,
)
@click.option(
    "--exclude-tools",
    help="Comma-separated list of tools to remove from the active set (e.g. 'PowerShell,Registry').",
    default=None,
    envvar="WINDOWS_MCP_EXCLUDE_TOOLS",
    type=str,
    show_default=False,
)
@click.option(
    "--cors-origins",
    help="Comma-separated list of allowed CORS origins (e.g. 'https://my-client.example.com'). Defaults to none — no CORS headers are emitted, so browsers block cross-origin requests via Same-Origin Policy.",
    default=None,
    envvar="WINDOWS_MCP_CORS_ORIGINS",
    type=str,
    show_default=False,
)
@click.option(
    "--ssl-certfile",
    help="Path to TLS certificate file (.pem) for HTTPS. Requires --ssl-keyfile.",
    default=None,
    envvar="WINDOWS_MCP_SSL_CERTFILE",
    type=str,
    show_default=False,
)
@click.option(
    "--ssl-keyfile",
    help="Path to TLS private key file (.pem) for HTTPS. Requires --ssl-certfile.",
    default=None,
    envvar="WINDOWS_MCP_SSL_KEYFILE",
    type=str,
    show_default=False,
)
@click.option(
    "--oauth-client-id",
    help="OAuth client ID (pre-provisioned confidential client). Requires --oauth-client-secret.",
    default=None,
    envvar="WINDOWS_MCP_OAUTH_CLIENT_ID",
    type=str,
    show_default=False,
)
@click.option(
    "--oauth-client-secret",
    help="OAuth client secret. Requires --oauth-client-id.",
    default=None,
    envvar="WINDOWS_MCP_OAUTH_CLIENT_SECRET",
    type=str,
    show_default=False,
)
@click.option(
    "--stateless-http",
    help="Run the streamable-http transport in stateless mode (no Mcp-Session-Id header). Lets reconnecting clients survive a server restart without re-handshaking, and removes the per-connection state that prevents horizontal scaling. Has no effect on stdio/sse transports.",
    is_flag=True,
    default=False,
    envvar="WINDOWS_MCP_STATELESS_HTTP",
    show_default=True,
)
def serve(
    ctx,
    transport,
    host,
    port,
    debug,
    config,
    auth_key,
    allow_insecure_remote,
    ip_allowlist,
    tools,
    exclude_tools,
    cors_origins,
    ssl_certfile,
    ssl_keyfile,
    oauth_client_id,
    oauth_client_secret,
    stateless_http,
):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if transport == Transport.STDIO.value:
        os.environ.setdefault("NO_COLOR", "1")
    if debug:
        enable_debug()
        logging.getLogger().setLevel(logging.DEBUG)
        for name in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastmcp"]:
            logging.getLogger(name).setLevel(logging.DEBUG)

    # Load config file and merge with CLI flags (CLI wins)
    config_path = discover_config_path(config)
    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc))

    transport = _choose_value(ctx, "transport", transport, cfg.server.transport, "stdio")
    host = _choose_value(ctx, "host", host, cfg.server.host, "localhost")
    port = int(_choose_value(ctx, "port", port, cfg.server.port, 8000))
    auth_key = _choose_value(ctx, "auth_key", auth_key, cfg.server.auth_key, None)
    stateless_http = bool(
        _choose_value(ctx, "stateless_http", stateless_http, cfg.server.stateless_http, False)
    )
    allow_insecure_remote = bool(
        _choose_value(
            ctx,
            "allow_insecure_remote",
            allow_insecure_remote,
            cfg.server.allow_insecure_remote,
            False,
        )
    )
    ssl_certfile = _choose_value(ctx, "ssl_certfile", ssl_certfile, cfg.server.ssl_certfile, None)
    ssl_keyfile = _choose_value(ctx, "ssl_keyfile", ssl_keyfile, cfg.server.ssl_keyfile, None)
    oauth_client_id = _choose_value(
        ctx, "oauth_client_id", oauth_client_id, cfg.security.oauth_client_id, None
    )
    oauth_client_secret = _choose_value(
        ctx, "oauth_client_secret", oauth_client_secret, cfg.security.oauth_client_secret, None
    )

    cli_tools = [t.strip() for t in tools.split(",") if t.strip()] if tools else []
    cli_exclude = (
        [t.strip() for t in exclude_tools.split(",") if t.strip()]
        if _param_explicit(ctx, "exclude_tools") and exclude_tools
        else list(cfg.tools.exclude)
    )
    cli_allowlist = (
        [e.strip() for e in ip_allowlist.split(",")]
        if ip_allowlist and _param_explicit(ctx, "ip_allowlist")
        else cfg.security.ip_allowlist
    )
    cli_cors = (
        [o.strip() for o in cors_origins.split(",") if o.strip()]
        if cors_origins and _param_explicit(ctx, "cors_origins")
        else list(cfg.security.cors_origins)
    )

    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise click.ClickException("--ssl-certfile and --ssl-keyfile must be provided together.")

    if bool(oauth_client_id) != bool(oauth_client_secret):
        raise click.ClickException(
            "OAuth requires both --oauth-client-id and --oauth-client-secret."
        )

    parsed_allowlist = None
    if cli_allowlist:
        try:
            parsed_allowlist = parse_ip_allowlist(cli_allowlist)
        except ValueError as exc:
            raise click.ClickException(f"Invalid ip_allowlist: {exc}")

    configured_oauth = bool(oauth_client_id and oauth_client_secret)

    if (
        transport != Transport.STDIO.value
        and not is_loopback_host(host)
        and not auth_key
        and not configured_oauth
        and not allow_insecure_remote
    ):
        raise click.ClickException(
            f"Refusing to bind HTTP transport to '{host}' without authentication.\n"
            "  Use --auth-key <token> or --oauth-client-id/--oauth-client-secret.\n"
            "  Or pass --allow-insecure-remote to explicitly allow unauthenticated access (not recommended)."
        )

    if (auth_key or cli_allowlist) and transport == Transport.STDIO.value:
        logger.warning("--auth-key / --ip-allowlist have no effect on stdio transport")

    # DNS rebinding protection: validate Host header against the bind address.
    # Applied automatically for loopback binds; skipped for 0.0.0.0/:: (too broad)
    # and when allow_insecure_remote is set.
    if transport != Transport.STDIO.value and not allow_insecure_remote:
        if is_loopback_host(host):
            computed_allowed_hosts: list[str] | None = ["localhost", "127.0.0.1", "[::1]"]
        elif host not in ("0.0.0.0", "::", ""):
            computed_allowed_hosts = [host]
        else:
            computed_allowed_hosts = None
    else:
        computed_allowed_hosts = None

    # Set up OAuth routes if configured (HTTP transports only)
    oauth_validator = None
    if configured_oauth and transport != Transport.STDIO.value:
        mcp = _build_mcp()
        oauth_store = OAuthStore()
        scheme = "https" if (ssl_certfile and ssl_keyfile) else "http"
        issuer = f"{scheme}://{host}:{port}"
        routes = build_oauth_routes(
            store=oauth_store,
            issuer=issuer,
            configured_client_id=oauth_client_id,
            configured_client_secret=oauth_client_secret,
        )
        for path, (handler, methods) in routes.items():
            mcp.custom_route(path, methods=methods)(handler)
        oauth_validator = lambda tok: validate_oauth_token(oauth_store, tok)  # noqa: E731

    scheme = "https" if ssl_certfile else "http"
    logger.debug(
        "Starting windows-mcp (transport=%s, %s, auth=%s, oauth=%s, ip-allowlist=%s, cors=%s, tools=%s, exclude=%s)",
        transport,
        scheme,
        "on" if auth_key else "off",
        "on" if configured_oauth else "off",
        cli_allowlist or "off",
        cli_cors or "off",
        cli_tools or "all",
        cli_exclude or "none",
    )
    try:
        _run_server(
            transport=transport,
            host=host,
            port=port,
            auth_key=auth_key,
            ip_allowlist=parsed_allowlist,
            explicit_tools=cli_tools or None,
            exclude_tools=cli_exclude or None,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            oauth_validator=oauth_validator,
            cors_origins=cli_cors or None,
            allowed_hosts=computed_allowed_hosts,
            stateless_http=stateless_http,
        )
        logger.debug("Server shut down normally")
    except Exception:
        logger.error("Server exiting due to unhandled exception", exc_info=True)
        raise


def _gen_tls(host: str, cert_path, key_path) -> None:
    """Generate a TLS cert/key pair, preferring mkcert over openssl."""
    from pathlib import Path

    cert_path = Path(cert_path)
    key_path = Path(key_path)

    mkcert = subprocess.run(["where", "mkcert"], capture_output=True).returncode == 0

    if mkcert:
        click.echo("mkcert detected — generating a locally-trusted certificate...")
        install = subprocess.run(["mkcert", "-install"], capture_output=True, text=True)
        if install.returncode != 0:
            raise click.ClickException(f"mkcert -install failed:\n{install.stderr.strip()}")

        sans = [host] if host not in ("0.0.0.0", "") else ["localhost", "127.0.0.1", "::1"]
        result = subprocess.run(
            [
                "mkcert",
                "-cert-file",
                str(cert_path),
                "-key-file",
                str(key_path),
                *sans,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise click.ClickException(f"mkcert failed:\n{result.stderr.strip()}")
        click.echo("  Certificate is automatically trusted by Windows.")
    else:
        click.echo("mkcert not found — falling back to openssl (self-signed)...")
        click.echo("  Tip: winget install FiloSottile.mkcert  for auto-trusted certs next time.")
        result = subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:4096",
                "-keyout",
                str(key_path),
                "-out",
                str(cert_path),
                "-days",
                "365",
                "-nodes",
                "-subj",
                f"/CN={host or 'windows-mcp'}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise click.ClickException(f"openssl failed:\n{result.stderr.strip()}")
        click.echo("  To make Windows trust this cert, run in an elevated PowerShell:")
        click.echo(
            f'    Import-Certificate -FilePath "{cert_path}" -CertStoreLocation Cert:\\LocalMachine\\Root'
        )

    click.echo(f"  cert → {cert_path}")
    click.echo(f"  key  → {key_path}")


_TASK_NAME = "windows-mcp-server"
_START_SCRIPT_PATH = CONFIG_DIR / "start-server.cmd"


def _resolve_program() -> list[str]:
    """Return the argv prefix to invoke `windows-mcp serve` from Task Scheduler.

    Uses the running interpreter so the wrapper always targets this exact
    installation, regardless of what (if anything) is on PATH.
    """
    return [sys.executable, "-m", "windows_mcp"]


def _build_start_script(program_args: list[str]) -> str:
    log_out = CONFIG_DIR / "server.log"
    log_err = CONFIG_DIR / "server.error.log"
    command = subprocess.list2cmdline(program_args)
    return f'@echo off\nsetlocal\n{command} 1>>"{log_out}" 2>>"{log_err}"\n'


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def _register_task_powershell(task_name: str, script_path: str) -> subprocess.CompletedProcess:
    """Register an ONLOGON scheduled task via PowerShell at RunLevel Limited.

    Unlike `schtasks /Create /SC ONLOGON`, Register-ScheduledTask with
    -RunLevel Limited does not require an elevated shell.
    """
    ps = (
        f"$action  = New-ScheduledTaskAction -Execute '{script_path}';"
        f"$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME;"
        f"$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;"
        f"Register-ScheduledTask -TaskName '{task_name}' -Action $action"
        f" -Trigger $trigger -Settings $set -RunLevel Limited -Force"
    )
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
        text=True,
    )


@main.command()
@click.option(
    "--transport",
    type=click.Choice(["sse", "streamable-http"]),
    default="streamable-http",
    show_default=True,
    help="Transport for the background server (stdio not supported as a service).",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="Host to bind.")
@click.option("--port", default=8000, show_default=True, type=int, help="Port to bind.")
@click.option("--force", is_flag=True, help="Reinstall even if already installed.")
def install(transport: str, host: str, port: int, force: bool) -> None:
    """Install windows-mcp as a scheduled task that starts at login."""
    query = _schtasks("/Query", "/TN", _TASK_NAME)
    if query.returncode == 0 and not force:
        click.echo(f"Scheduled task '{_TASK_NAME}' is already installed.")
        click.echo("Use --force to reinstall.")
        return

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    exe = _resolve_program()
    args = exe + ["serve", "--transport", transport, "--host", host, "--port", str(port)]
    _START_SCRIPT_PATH.write_text(_build_start_script(args), encoding="utf-8")

    # Remove any existing task first when forcing a reinstall.
    _schtasks("/Delete", "/TN", _TASK_NAME, "/F")

    result = _register_task_powershell(_TASK_NAME, str(_START_SCRIPT_PATH))
    if result.returncode != 0:
        raise click.ClickException(
            f"Register-ScheduledTask failed:\n{result.stderr.strip() or result.stdout.strip()}"
        )

    run_result = _schtasks("/Run", "/TN", _TASK_NAME)
    if run_result.returncode != 0:
        raise click.ClickException(
            f"schtasks /Run failed:\n{run_result.stderr.strip() or run_result.stdout.strip()}"
        )

    click.echo("Scheduled task installed — server is starting now.")
    click.echo(f"  Task      : {_TASK_NAME}")
    click.echo(f"  Transport : {transport}")
    click.echo(f"  Address   : {host}:{port}")
    click.echo(f"  Logs      : {CONFIG_DIR / 'server.log'}")
    click.echo("\nThe server will restart automatically at every login.")
    click.echo("Run `windows-mcp uninstall` to remove it.")


@main.command()
def uninstall() -> None:
    """Remove the windows-mcp scheduled task and stop the background server."""
    stop_result = _schtasks("/End", "/TN", _TASK_NAME)
    if stop_result.returncode == 0:
        click.echo("Stopped the running server.")

    delete_result = _schtasks("/Delete", "/TN", _TASK_NAME, "/F")
    if delete_result.returncode == 0:
        click.echo(f"Removed scheduled task '{_TASK_NAME}'.")
    else:
        click.echo("No scheduled task found.")

    if _START_SCRIPT_PATH.exists():
        _START_SCRIPT_PATH.unlink()
        click.echo(f"Removed {_START_SCRIPT_PATH}")

    click.echo("windows-mcp will no longer start at login.")


@main.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="sse",
    show_default=True,
    help="Transport mode to configure. Saves the choice to config.toml.",
)
@click.option(
    "--host",
    default="0.0.0.0",
    show_default=True,
    help="Host to bind the server to.",
)
@click.option(
    "--port",
    default=8000,
    show_default=True,
    type=int,
    help="Port to bind the server to.",
)
@click.option("--with-tls", is_flag=True, help="Generate a self-signed TLS certificate and key.")
@click.option("--force", is_flag=True, help="Overwrite existing credentials without prompting.")
def auth(transport: str, host: str, port: int, with_tls: bool, force: bool) -> None:
    """Generate an auth key (and optionally TLS certs) and save to ~/.windows-mcp/config.toml."""
    config_path = CONFIG_FILE

    cfg = load_config(config_path) if config_path.exists() else WindowsMCPConfig()

    if cfg.server.auth_key and not force:
        click.echo(f"Auth key already set in {config_path}. Use --force to regenerate.")
        return

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    new_key = secrets.token_urlsafe(32)
    cfg.server.auth_key = new_key
    cfg.server.transport = transport
    cfg.server.host = host
    cfg.server.port = port
    click.echo(f"Generated auth key: {new_key}")

    if with_tls:
        if transport == "stdio":
            raise click.ClickException("TLS has no effect on stdio transport.")
        cert_path = CONFIG_DIR / "cert.pem"
        key_path = CONFIG_DIR / "key.pem"
        _gen_tls(host, cert_path, key_path)
        cfg.server.ssl_certfile = str(cert_path)
        cfg.server.ssl_keyfile = str(key_path)

    write_config(cfg, config_path)
    click.echo(f"\nSaved to {config_path}")

    if transport == "stdio":
        _echo_section("Claude Desktop config (stdio)")
        click.echo(
            """\
{
  "mcpServers": {
    "windows-mcp": {
      "command": "uvx",
      "args": ["windows-mcp", "serve"]
    }
  }
}"""
        )
        return

    scheme = "https" if with_tls else "http"
    mcp_url = f"{scheme}://{host}:{port}/mcp/"
    sse_url = f"{scheme}://{host}:{port}/sse"

    _echo_section("Start the server")
    click.echo("  windows-mcp serve")

    if transport == "sse":
        _echo_section("Claude Desktop config (SSE)")
        click.echo(
            f"""\
{{
  "mcpServers": {{
    "windows-mcp": {{
      "type": "sse",
      "url": "{sse_url}",
      "headers": {{ "Authorization": "Bearer {new_key}" }}
    }}
  }}
}}"""
        )
    else:
        _echo_section("Claude Desktop config (Streamable HTTP)")
        click.echo(
            f"""\
{{
  "mcpServers": {{
    "windows-mcp": {{
      "type": "http",
      "url": "{mcp_url}",
      "headers": {{ "Authorization": "Bearer {new_key}" }}
    }}
  }}
}}"""
        )


if __name__ == "__main__":
    main()
