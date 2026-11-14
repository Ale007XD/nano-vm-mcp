"""tests/test_auth.py — BearerAuthMiddleware + /health endpoint tests.

Topology under test:
    Starlette (top-level, no middleware)
    ├── /health   → public
    └── Mount("/") → protected sub-app
            ├── BearerAuthMiddleware
            └── /sse  (stub)
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from nano_vm_mcp.auth import AuthPolicy
from nano_vm_mcp.server import BearerAuthMiddleware


# ---------------------------------------------------------------------------
# Test app — mirrors run_sse() topology
# ---------------------------------------------------------------------------


async def _sse_stub(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def _health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


KEY = AuthPolicy(mode="key", key="secret-token")
NOAUTH = AuthPolicy(mode="noauth")


def _make_app(policy: AuthPolicy) -> Starlette:
    protected = Starlette(
        routes=[Route("/sse", endpoint=_sse_stub)],
        middleware=[Middleware(BearerAuthMiddleware, policy=policy)],
    )
    return Starlette(
        routes=[
            Route("/health", endpoint=_health),
            Mount("/", app=protected),
        ],
    )


_key_app = _make_app(KEY)
_noauth_app = _make_app(NOAUTH)


# ---------------------------------------------------------------------------
# /health — always public, never touches auth middleware
# ---------------------------------------------------------------------------


def test_health_public_noauth():
    client = TestClient(_noauth_app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_public_with_key_set_no_header():
    """Even when API key is configured, /health must be reachable without a token."""
    client = TestClient(_key_app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_public_with_key_set_wrong_header():
    """Wrong token must not block /health."""
    client = TestClient(_key_app)
    resp = client.get("/health", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /sse — protected by BearerAuthMiddleware
# ---------------------------------------------------------------------------


def test_sse_noauth_policy_allows_all():
    """Revised (G-23): this used to pin fail-open (no key -> allow). Now the open state is
    reachable only through an explicit 'noauth' policy (NANO_VM_MCP_ALLOW_NOAUTH=1)."""
    client = TestClient(_noauth_app)
    assert client.get("/sse").status_code == 200


def test_sse_valid_token_passes():
    client = TestClient(_key_app)
    resp = client.get("/sse", headers={"Authorization": "Bearer secret-token"})
    assert resp.status_code == 200


def test_sse_missing_header_rejected():
    client = TestClient(_key_app)
    assert client.get("/sse").status_code == 401


def test_sse_wrong_token_rejected():
    client = TestClient(_key_app)
    resp = client.get("/sse", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_sse_malformed_header_rejected():
    client = TestClient(_key_app)
    resp = client.get("/sse", headers={"Authorization": "secret-token"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth failure logging
# ---------------------------------------------------------------------------


def test_auth_failure_is_logged(caplog):
    import logging

    client = TestClient(_key_app)

    with caplog.at_level(logging.WARNING, logger="nano_vm_mcp.server"):
        client.get("/sse", headers={"Authorization": "Bearer wrong"})

    assert any("auth_failed" in r.message for r in caplog.records)


def test_auth_success_is_not_logged(caplog):
    import logging

    client = TestClient(_key_app)

    with caplog.at_level(logging.WARNING, logger="nano_vm_mcp.server"):
        client.get("/sse", headers={"Authorization": "Bearer secret-token"})

    assert not any("auth_failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Header edge cases
# ---------------------------------------------------------------------------


def test_sse_non_ascii_token_is_401_not_500():
    """secrets.compare_digest(str, str) raises TypeError on non-ASCII; must be a clean 401."""
    client = TestClient(_key_app, raise_server_exceptions=False)
    resp = client.get("/sse", headers={"Authorization": "Bearer é".encode("latin-1")})
    assert resp.status_code == 401


def test_sse_token_with_surrounding_spaces_matches():
    client = TestClient(_key_app)
    resp = client.get("/sse", headers={"Authorization": "Bearer  secret-token "})
    assert resp.status_code == 200
