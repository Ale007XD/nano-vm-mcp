"""nano_vm_mcp.server — MCP server with stdio and SSE transports."""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.types import Tool, TextContent

from .store import ProgramStore
from . import tools as _tools

_DB_PATH = os.getenv("NANO_VM_MCP_DB", "nano_vm_mcp.db")

_store = ProgramStore(_DB_PATH)
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
# Tool dispatch
# ---------------------------------------------------------------------------


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "run_program":
        result = await _tools.run_program(
            _store,
            arguments["program"],
            arguments.get("save_as", ""),
        )
    elif name == "get_trace":
        result = await _tools.get_trace(_store, arguments["trace_id"])
    elif name == "list_programs":
        result = await _tools.list_programs(_store)
    elif name == "get_program":
        result = await _tools.get_program(_store, arguments["program_id"])
    elif name == "delete_program":
        result = await _tools.delete_program(_store, arguments["program_id"])
    else:
        result = {"error": f"Unknown tool: {name}"}

    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


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
                        notification_options=None,
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
    from starlette.routing import Route, Mount

    sse = SseServerTransport("/messages")

    async def handle_sse(request: Any) -> Any:
        async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
            await app.run(
                r,
                w,
                InitializationOptions(
                    server_name="nano-vm-mcp",
                    server_version="0.1.0",
                    capabilities=app.get_capabilities(
                        notification_options=None,
                        experimental_capabilities={},
                    ),
                ),
            )

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages", app=sse.handle_post_message),
        ]
    )
    uvicorn.run(starlette_app, host=host, port=port)
