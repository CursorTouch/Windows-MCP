"""Minimal OAuth 2.0 Authorization Server for MCP spec compatibility.

Supports:
- RFC 8414  /.well-known/oauth-authorization-server  metadata
- RFC 7591  POST /oauth/register  (disabled — pre-provisioned clients only)
- RFC 7636  Authorization Code + PKCE (S256 required)
- GET  /oauth/authorize   authorization endpoint
- POST /oauth/token        token exchange
"""

from __future__ import annotations

import hashlib
import base64
import ipaddress
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

TOKEN_LIFETIME = 3600   # 1 hour
CODE_LIFETIME = 300     # 5 minutes


# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------


@dataclass
class RegisteredClient:
    client_id: str
    client_secret: str | None = None
    redirect_uris: list[str] = field(default_factory=list)


@dataclass
class AuthorizationCode:
    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    expires_at: float = 0.0


@dataclass
class AccessToken:
    token: str
    client_id: str
    expires_at: float = 0.0


class OAuthStore:
    """In-memory store for OAuth state (single-process)."""

    def __init__(self) -> None:
        self.clients: dict[str, RegisteredClient] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.tokens: dict[str, AccessToken] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_pkce(code_verifier: str, code_challenge: str, method: str) -> bool:
    if method == "S256":
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return secrets.compare_digest(expected, code_challenge)
    if method == "plain":
        return secrets.compare_digest(code_verifier, code_challenge)
    return False


def _is_loopback_redirect_uri(redirect_uri: str) -> bool:
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.fragment:
        return False
    host = parsed.hostname.lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def build_oauth_routes(
    store: OAuthStore,
    issuer: str,
    configured_client_id: str | None = None,
    configured_client_secret: str | None = None,
):
    """Return a dict of {path: (handler, methods)} for OAuth endpoints."""

    if configured_client_id:
        store.clients[configured_client_id] = RegisteredClient(
            client_id=configured_client_id,
            client_secret=configured_client_secret,
        )

    async def metadata(request: Request):
        return JSONResponse({
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
        })

    async def register(request: Request):
        return JSONResponse(
            {
                "error": "registration_disabled",
                "error_description": (
                    "Dynamic OAuth client registration is disabled. "
                    "Configure --oauth-client-id and --oauth-client-secret on the server."
                ),
            },
            status_code=403,
        )

    async def authorize(request: Request):
        params = request.query_params
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        response_type = params.get("response_type", "")
        state = params.get("state", "")
        code_challenge = params.get("code_challenge", "")
        code_challenge_method = params.get("code_challenge_method", "S256")

        if response_type != "code":
            return JSONResponse({"error": "unsupported_response_type"}, status_code=400)

        if not code_challenge:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "code_challenge required (PKCE)"},
                status_code=400,
            )

        if code_challenge_method != "S256":
            return JSONResponse(
                {"error": "invalid_request", "error_description": "code_challenge_method must be S256"},
                status_code=400,
            )

        if not _is_loopback_redirect_uri(redirect_uri):
            return JSONResponse(
                {"error": "invalid_request", "error_description": "redirect_uri must be a loopback http(s) URI"},
                status_code=400,
            )

        if not configured_client_id or not configured_client_secret:
            return JSONResponse(
                {"error": "server_error", "error_description": "OAuth requires a configured client ID and secret"},
                status_code=500,
            )

        if client_id != configured_client_id:
            return JSONResponse({"error": "invalid_client"}, status_code=400)

        client = store.clients[client_id]
        if redirect_uri not in client.redirect_uris:
            client.redirect_uris.append(redirect_uri)

        code = secrets.token_urlsafe(32)
        store.codes[code] = AuthorizationCode(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            expires_at=time.time() + CODE_LIFETIME,
        )

        sep = "&" if "?" in redirect_uri else "?"
        location = f"{redirect_uri}{sep}code={urllib.parse.quote(code)}"
        if state:
            location += f"&state={urllib.parse.quote(state)}"
        return RedirectResponse(location, status_code=302)

    async def token(request: Request):
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            body = dict(form)
        elif "application/json" in content_type:
            body = await request.json()
        else:
            try:
                form = await request.form()
                body = dict(form)
            except Exception:
                return JSONResponse({"error": "invalid_request"}, status_code=400)

        if body.get("grant_type", "") != "authorization_code":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        code_value = body.get("code", "")
        code_verifier = body.get("code_verifier", "")
        client_id = body.get("client_id", "")
        redirect_uri = body.get("redirect_uri", "")

        auth_code = store.codes.get(code_value)
        if not auth_code:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "unknown code"}, status_code=400
            )

        if auth_code.client_id != client_id:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "client_id mismatch"}, status_code=400
            )

        if auth_code.redirect_uri != redirect_uri:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "redirect_uri mismatch"}, status_code=400
            )

        if time.time() > auth_code.expires_at:
            del store.codes[code_value]
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "code expired"}, status_code=400
            )

        if not _verify_pkce(code_verifier, auth_code.code_challenge, auth_code.code_challenge_method):
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400
            )

        del store.codes[code_value]  # one-time use

        client = store.clients.get(client_id)
        if not client or not client.client_secret:
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        provided_secret = body.get("client_secret", "")
        if not provided_secret or not secrets.compare_digest(provided_secret, client.client_secret):
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        access_token = secrets.token_urlsafe(48)
        store.tokens[access_token] = AccessToken(
            token=access_token,
            client_id=client_id,
            expires_at=time.time() + TOKEN_LIFETIME,
        )

        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": TOKEN_LIFETIME,
        })

    return {
        "/.well-known/oauth-authorization-server": (metadata, ["GET"]),
        "/oauth/register": (register, ["POST"]),
        "/oauth/authorize": (authorize, ["GET"]),
        "/oauth/token": (token, ["POST"]),
    }


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------


def validate_oauth_token(store: OAuthStore, token: str) -> bool:
    """Return True if the token is a valid, non-expired OAuth access token."""
    at = store.tokens.get(token)
    if at is None:
        return False
    if time.time() > at.expires_at:
        del store.tokens[token]
        return False
    return True
