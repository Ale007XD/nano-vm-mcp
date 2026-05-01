"""nano_vm_mcp.cli — CLI entry point for nano-vm-mcp."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="nano-vm-mcp",
        description="MCP server for llm-nano-vm",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport mode: stdio (default, for Claude Desktop) or sse (HTTP, for VPS).",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("NANO_VM_MCP_HOST", "0.0.0.0"),
        help="SSE bind host (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("NANO_VM_MCP_PORT", "8080")),
        help="SSE bind port (default: 8080).",
    )
    parser.add_argument(
        "--db",
        default=os.getenv("NANO_VM_MCP_DB", "nano_vm_mcp.db"),
        help="Path to SQLite database (default: nano_vm_mcp.db).",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: .env).",
    )

    args = parser.parse_args()

    # Load .env before importing server (server reads env at module level)
    env_path = Path(args.env_file)
    if env_path.exists():
        from dotenv import load_dotenv

        load_dotenv(env_path)

    os.environ.setdefault("NANO_VM_MCP_DB", args.db)

    from .server import run_stdio, run_sse

    if args.transport == "stdio":
        run_stdio()
    else:
        _api_key = os.getenv("NANO_VM_MCP_API_KEY", "")
        if not _api_key:
            print(
                "\n"
                "  ⚠️  WARNING: NANO_VM_MCP_API_KEY is not set.\n"
                "     The SSE endpoint has NO authentication.\n"
                "     Anyone who knows the URL can call run_program on this server.\n"
                "\n"
                "     Set NANO_VM_MCP_API_KEY in your .env or environment before\n"
                "     exposing this server outside localhost or a trusted network.\n",
                file=sys.stderr,
            )
        run_sse(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
