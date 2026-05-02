# tests/test_auth.py
"""Tests for SSE bearer auth middleware."""

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from nano_vm_mcp.server import BearerAuthMiddleware


async def _homepage(request):
    return JSONResponse({"ok": True})


_app = Starlette(
    routes=[Route("/sse", endpoint=_homepage)],
    middleware=[Middleware(BearerAuthMiddleware)],
)


def test_auth_no_key_configured_allows_all(monkeypatch):
    monkeypatch.delenv("NANO_VM_MCP_API_KEY", raising=False)
    client = TestClient(_app)
    assert client.get("/sse").status_code == 200


def test_auth_valid_token_passes(monkeypatch):
    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)
    resp = client.get("/sse", headers={"Authorization": "Bearer secret-token"})
    assert resp.status_code == 200


def test_auth_missing_header_rejected(monkeypatch):
    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)
    resp = client.get("/sse")
    assert resp.status_code == 401


def test_auth_wrong_token_rejected(monkeypatch):
    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)
    resp = client.get("/sse", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_auth_malformed_header_rejected(monkeypatch):
    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "secret-token")
    client = TestClient(_app)
    resp = client.get("/sse", headers={"Authorization": "secret-token"})
    assert resp.status_code == 401


# Removed duplicate/legacy tests; new tests above cover all scenarios.
