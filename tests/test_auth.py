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

from nano_vm_mcp.server import BearerAuthMiddleware


# ---------------------------------------------------------------------------
# Test app — mirrors run_sse() topology
# ---------------------------------------------------------------------------


async def _sse_stub(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def _health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _make_app() -> Starlette:
    protected = Starlette(
        routes=[Route("/sse", endpoint=_sse_stub)],
        middleware=[Middleware(BearerAuthMiddleware)],
    )
    return Starlette(
        routes=[
            Route("/health", endpoint=_health),
            Mount("/", app=protected),
        ],
    )


_app = _make_app()


# ---------------------------------------------------------------------------
# /health — always public, never touches auth middleware
# ---------------------------------------------------------------------------


def test_health_public_no_key(monkeypatch):
    monkeypatch.delenv("NANO_VM_MCP_API_KEY", raising=False)
    client = TestClient(_app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_public_with_key_set_no_header(monkeypatch):
    """Even when API key is configured, /health must be reachable without a token."""
    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_public_with_key_set_wrong_header(monkeypatch):
    """Wrong token must not block /health."""
    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)
    resp = client.get("/health", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /sse — protected by BearerAuthMiddleware
# ---------------------------------------------------------------------------


def test_sse_no_key_configured_allows_all(monkeypatch):
    monkeypatch.delenv("NANO_VM_MCP_API_KEY", raising=False)
    client = TestClient(_app)
    assert client.get("/sse").status_code == 200


def test_sse_valid_token_passes(monkeypatch):
    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)
    resp = client.get("/sse", headers={"Authorization": "Bearer secret-token"})
    assert resp.status_code == 200


def test_sse_missing_header_rejected(monkeypatch):
    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)
    assert client.get("/sse").status_code == 401


def test_sse_wrong_token_rejected(monkeypatch):
    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)
    resp = client.get("/sse", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_sse_malformed_header_rejected(monkeypatch):
    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)
    resp = client.get("/sse", headers={"Authorization": "secret-token"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth failure logging
# ---------------------------------------------------------------------------


def test_auth_failure_is_logged(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)

    with caplog.at_level(logging.WARNING, logger="nano_vm_mcp.server"):
        client.get("/sse", headers={"Authorization": "Bearer wrong"})

    assert any("auth_failed" in r.message for r in caplog.records)


def test_auth_success_is_not_logged(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)

    with caplog.at_level(logging.WARNING, logger="nano_vm_mcp.server"):
        client.get("/sse", headers={"Authorization": "Bearer secret-token"})

    assert not any("auth_failed" in r.message for r in caplog.records)
