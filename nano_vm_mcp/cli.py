"""nano_vm_mcp.cli — CLI entry point for nano-vm-mcp."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .auth import TRANSPORTS, AuthConfigError, resolve_auth_policy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nano-vm-mcp",
        description="MCP server for llm-nano-vm",
    )
    parser.add_argument(
        "--transport",
        choices=list(TRANSPORTS),
        default="stdio",
        help=(
            "Transport mode: stdio (default, local, for Claude Desktop) or sse (HTTP, for VPS). "
            "Every transport except stdio requires NANO_VM_MCP_API_KEY "
            "(or NANO_VM_MCP_ALLOW_NOAUTH=1)."
        ),
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
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    # Load .env before importing server (server reads env at module level)
    env_path = Path(args.env_file)
    if env_path.exists():
        from dotenv import load_dotenv

        load_dotenv(env_path)

    # Single auth decision point: before any transport branch and before the server
    # module is imported (importing it opens the SQLite database).
    try:
        policy = resolve_auth_policy(args.transport, os.environ)
    except AuthConfigError as exc:
        print(f"\n  nano-vm-mcp: refusing to start.\n  {exc}\n", file=sys.stderr)
        raise SystemExit(2) from None

    os.environ.setdefault("NANO_VM_MCP_DB", args.db)

    from .server import run_sse, run_stdio

    if args.transport == "stdio":
        run_stdio()
    elif args.transport == "sse":
        run_sse(policy, host=args.host, port=args.port)
    else:  # a transport added to TRANSPORTS without a runner must not start unauthenticated
        print(f"nano-vm-mcp: no runner for transport '{args.transport}'", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
