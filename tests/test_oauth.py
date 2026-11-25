"""Tests for the built-in OAuth 2.0 Authorization Code + PKCE endpoints."""

import base64
import hashlib
import urllib.parse

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from windows_mcp.infrastructure.oauth import (
    OAuthStore,
    build_oauth_routes,
    validate_oauth_token,
)

CLIENT_ID = "mcp-client"
CLIENT_SECRET = "super-secret-value"
REDIRECT_URI = "http://127.0.0.1:9999/callback"


def _pkce_pair(verifier: str = "a" * 43) -> tuple[str, str]:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@pytest.fixture
def oauth_client() -> tuple[TestClient, OAuthStore]:
    store = OAuthStore()
    routes = build_oauth_routes(
        store=store,
        issuer="http://127.0.0.1:8000",
        configured_client_id=CLIENT_ID,
        configured_client_secret=CLIENT_SECRET,
    )
    app = Starlette(
        routes=[Route(path, handler, methods=methods) for path, (handler, methods) in routes.items()]
    )
    return TestClient(app, raise_server_exceptions=False), store


def _mint_code(client: TestClient, challenge: str) -> str:
    resp = client.get(
        "/oauth/authorize",
        params={
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "opaque-state",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    query = urllib.parse.parse_qs(urllib.parse.urlparse(resp.headers["location"]).query)
    assert query["state"] == ["opaque-state"]
    return query["code"][0]


def test_authorization_code_pkce_exchange_and_one_time_use(oauth_client):
    client, store = oauth_client
    verifier, challenge = _pkce_pair()
    code = _mint_code(client, challenge)

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert validate_oauth_token(store, body["access_token"])

    # Code cannot be replayed after successful exchange.
    replay = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


def test_wrong_client_secret_rejects_without_consuming_code(oauth_client):
    client, store = oauth_client
    verifier, challenge = _pkce_pair()
    code = _mint_code(client, challenge)

    bad = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": CLIENT_ID,
            "client_secret": "wrong-secret",
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert bad.status_code == 401
    assert bad.json()["error"] == "invalid_client"
    assert code in store.codes

    # Legitimate client can still redeem the untouched authorization code.
    good = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert good.status_code == 200


def test_missing_client_secret_returns_401_without_probing_codes(oauth_client):
    client, _ = oauth_client
    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": "nonexistent-code",
            "code_verifier": "a" * 43,
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


@pytest.mark.parametrize(
    "raw_body",
    [b"{malformed", b"[]", b"null", b'"string"', b"123"],
)
def test_token_endpoint_rejects_invalid_or_non_object_json_with_400(oauth_client, raw_body):
    client, _ = oauth_client
    resp = client.post(
        "/oauth/token",
        content=raw_body,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("grant_type", ["authorization_code"]),
        ("code", ["unhashable"]),
        ("code_verifier", 12345),
        ("client_id", {"nested": True}),
        ("client_secret", 999),
        ("redirect_uri", False),
    ],
)
def test_token_endpoint_rejects_non_string_json_fields_with_400(oauth_client, field, bad_value):
    client, _ = oauth_client
    payload = {
        "grant_type": "authorization_code",
        "code": "some-code",
        "code_verifier": "a" * 43,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
    }
    payload[field] = bad_value
    resp = client.post("/oauth/token", json=payload)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"


def test_token_endpoint_rejects_non_ascii_code_verifier_without_500(oauth_client):
    client, _ = oauth_client
    _, challenge = _pkce_pair()
    code = _mint_code(client, challenge)

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": "caf\u00e9-non-ascii",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"
