"""nano_vm_mcp.server — MCP server with stdio and SSE transports."""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any

from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.types import TextContent, Tool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .handlers import build_chain
from .store import ProgramStore

logger = logging.getLogger(__name__)

_DB_PATH = os.getenv("NANO_VM_MCP_DB", "nano_vm_mcp.db")

_store = ProgramStore(_DB_PATH)
_chain = build_chain()


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject SSE requests without a valid Bearer token when API key is configured."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        api_key = os.getenv("NANO_VM_MCP_API_KEY", "")
        if not api_key:
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not secrets.compare_digest(
            auth[len("Bearer "):].strip(), api_key
        ):
            logger.warning(
                "auth_failed method=%s path=%s client=%s",
                request.method,
                request.url.path,
                request.client.host if request.client else "unknown",
            )
            return Response(
                content='{"error": "Unauthorized"}',
                status_code=401,
                media_type="application/json",
            )
        return await call_next(request)


app = Server("nano-vm-mcp")


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="run_program",
            description=(
                "Execute a nano-vm Program dict. "
                "Returns trace_id, status, step count, and cost. "
                "Optionally persists the program under a name."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "program": {
                        "type": "object",
                        "description": "nano_vm.Program JSON (steps, budgets, etc.)",
                    },
                    "save_as": {
                        "type": "string",
                        "description": "Optional name to save the program for later reuse.",
                        "default": "",
                    },
                },
                "required": ["program"],
            },
        ),
        Tool(
            name="get_trace",
            description="Retrieve the full Trace JSON for a completed run by trace_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "trace_id": {"type": "string", "description": "UUID returned by run_program."},
                },
                "required": ["trace_id"],
            },
        ),
        Tool(
            name="list_programs",
            description="List all saved programs (id, name, created_at).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_program",
            description="Retrieve a saved Program JSON by its program_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
        ),
        Tool(
            name="delete_program",
            description="Delete a saved program and all its traces.",
            inputSchema={
                "type": "object",
                "properties": {
                    "program_id": {"type": "string"},
                },
                "required": ["program_id"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool dispatch — Chain of Responsibility (no if/else)
# ---------------------------------------------------------------------------


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    return await _chain.handle(name, arguments, _store)


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------


def run_stdio() -> None:
    """Start server in stdio mode (Claude Desktop / local MCP client)."""
    import asyncio

    from mcp.server.stdio import stdio_server

    async def _main() -> None:
        async with stdio_server() as (r, w):
            await app.run(
                r,
                w,
                InitializationOptions(
                    server_name="nano-vm-mcp",
                    server_version="0.1.0",
                    capabilities=app.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )

    asyncio.run(_main())


def run_sse(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start server in SSE/HTTP mode (VPS, remote MCP clients)."""
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.routing import Mount, Route

    sse = SseServerTransport("/messages")

    async def handle_sse(request: Request) -> Any:
        async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
            await app.run(
                r,
                w,
                InitializationOptions(
                    server_name="nano-vm-mcp",
                    server_version="0.1.0",
                    capabilities=app.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Protected sub-app: BearerAuthMiddleware applies only to /sse and /messages.
    protected = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages", app=sse.handle_post_message),
        ],
        middleware=[Middleware(BearerAuthMiddleware)],
    )

    # Top-level app: /health is public, all other paths go to protected sub-app.
    starlette_app = Starlette(
        routes=[
            Route("/health", endpoint=health),
            Mount("/", app=protected),
        ],
    )
    uvicorn.run(starlette_app, host=host, port=port)
