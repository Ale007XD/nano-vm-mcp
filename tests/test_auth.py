# tests/test_auth.py
"""Tests for SSE bearer auth middleware."""

import os
import pytest
from unittest.mock import patch, AsyncMock
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.middleware import Middleware

from nano_vm_mcp.server import BearerAuthMiddleware


def _make_app(api_key: str) -> Starlette:
    """Build minimal Starlette app with BearerAuthMiddleware."""
    async def homepage(request):
        return JSONResponse({"ok": True})

    with patch.dict(os.environ, {"NANO_VM_MCP_API_KEY": api_key}):
        return Starlette(
            routes=[Route("/sse", endpoint=homepage)],
            middleware=[Middleware(BearerAuthMiddleware)],
        )


def test_auth_no_key_configured_allows_all():
    """If NANO_VM_MCP_API_KEY is not set, all requests pass through."""
    app = _make_app("")
    client = TestClient(app)
    resp = client.get("/sse")
    assert resp.status_code == 200


def test_auth_valid_token_passes():
    """Valid bearer token returns 200."""
    app = _make_app("secret-token")
    client = TestClient(app)
    resp = client.get("/sse", headers={"Authorization": "Bearer secret-token"})
    assert resp.status_code == 200


def test_auth_missing_header_rejected():
    """No Authorization header → 401."""
    app = _make_app("secret-token")
    client = TestClient(app)
    resp = client.get("/sse")
    assert resp.status_code == 401
    assert resp.json() == {"error": "Unauthorized"}


def test_auth_wrong_token_rejected():
    """Wrong token → 401."""
    app = _make_app("secret-token")
    client = TestClient(app)
    resp = client.get("/sse", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_auth_malformed_header_rejected():
    """Malformed Authorization (no Bearer prefix) → 401."""
    app = _make_app("secret-token")
    client = TestClient(app)
    resp = client.get("/sse", headers={"Authorization": "secret-token"})
    assert resp.status_code == 401
