# nano-vm-mcp

<p align="center">
  <a href="https://github.com/Ale007XD/nano-vm-mcp/actions">
    <img src="https://github.com/Ale007XD/nano-vm-mcp/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <a href="https://pypi.org/project/nano-vm-mcp/">
    <img src="https://img.shields.io/pypi/v/nano-vm-mcp" alt="PyPI">
  </a>
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/MCP-compatible-purple" alt="MCP">
</p>

MCP server for [llm-nano-vm](https://github.com/Ale007XD/nano_vm) — run deterministic LLM programs
via the [Model Context Protocol](https://modelcontextprotocol.io/).

## Tools

| Tool | Description |
| :--- | :--- |
| `run_program` | Execute a `Program` dict → returns `trace_id`, status, step count, cost |
| `get_trace` | Retrieve full `Trace` JSON by `trace_id` |
| `list_programs` | List saved programs (`id`, `name`, `created_at`) |
| `get_program` | Retrieve saved `Program` JSON by `program_id` |
| `delete_program` | Delete a program and all its traces |

## Install

```bash
pip install nano-vm-mcp
```

## Usage

### stdio — Claude Desktop / local MCP client

```bash
nano-vm-mcp --transport stdio
```

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nano-vm-mcp": {
      "command": "nano-vm-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

### SSE — VPS / remote clients

```bash
nano-vm-mcp --transport sse --port 8080
```

MCP client URL: `http://<host>:8080/sse`

### Docker Compose (VPS)

```yaml
services:
  nano-vm-mcp:
    image: ghcr.io/ale007xd/nano-vm-mcp:latest
    ports:
      - "8080:8080"
    volumes:
      - ./data:/data
    environment:
      NANO_VM_MCP_DB: /data/nano_vm_mcp.db
      NANO_VM_MCP_PORT: 8080
    command: ["nano-vm-mcp", "--transport", "sse"]
```

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

| Variable | Default | Description |
| :--- | :--- | :--- |
| `NANO_VM_MCP_DB` | `nano_vm_mcp.db` | SQLite WAL database path |
| `NANO_VM_MCP_HOST` | `0.0.0.0` | SSE bind host |
| `NANO_VM_MCP_PORT` | `8080` | SSE bind port |

## Example: run a program

```python
import json, asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client

program = {
    "steps": [
        {"id": "s1", "type": "tool", "tool": "my_tool", "input": {"query": "hello"}}
    ]
}

async def main():
    async with sse_client("http://localhost:8080/sse") as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool("run_program", {"program": program, "save_as": "demo"})
            print(result.content[0].text)

asyncio.run(main())
```

## Security

### Condition expressions

`run_program` accepts a full Program dict — including `condition` steps with
arbitrary expression strings. These are evaluated via `eval()` with `__builtins__`
cleared. This is a partial sandbox, not full isolation.

**Rules for safe use:**

- Condition logic must be authored by you, not generated from untrusted input at runtime.
- LLM output may appear as a *value being tested* (`'yes' in '$decision'`), never as the
  condition expression itself.
- If you expose this MCP server to untrusted clients, validate or allowlist condition
  expressions before passing them to `run_program`.

### Tool registry

`ExecutionVM` only calls tools that are explicitly registered in its tool registry.
Unregistered tool names raise `VMError` — they are not silently executed.

Avoid registering destructive or privileged tools (filesystem writes, shell exec,
database mutations) without an explicit access control layer in your tool implementation.

### SSE transport and auth

The SSE transport (`--transport sse`) has **no authentication** in v0.1.0.
Anyone who knows the URL can call `run_program` on your server.

**Do not expose the SSE endpoint to the public internet without a reverse proxy
with auth** (e.g. nginx + bearer token, Cloudflare Access, or a VPN).
Auth middleware is on the roadmap for v0.2.0.

---

## Roadmap

- [x] `run_program`, `get_trace`, `list_programs`, `get_program`, `delete_program` (v0.1.0)
- [x] stdio + SSE transports
- [x] SQLite WAL persistence
- [x] Auth middleware for SSE — NANO_VM_MCP_API_KEY bearer token, timing-safe (v0.2.0)
- [ ] `plan_and_run` — intent string → Planner → run (P7)
- [ ] Docker image to GHCR
