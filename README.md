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

For programs with `llm` steps, install the LiteLLM extra:

```bash
pip install 'nano-vm-mcp[litellm]'
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
NANO_VM_MCP_API_KEY=your-secret-token nano-vm-mcp --transport sse --port 8080
```

MCP client URL: `http://<host>:8080/sse`

With auth header: `Authorization: Bearer your-secret-token`

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
      NANO_VM_MCP_API_KEY: your-secret-token
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
| `NANO_VM_MCP_API_KEY` | _(unset)_ | Bearer token for SSE auth. If unset, all requests are allowed (warning logged) |
| `NANO_VM_MCP_LLM_MODEL` | _(unset)_ | LiteLLM model string for `llm` steps (e.g. `openrouter/meta-llama/llama-3.3-70b-instruct:free`) |

## Endpoints

| Path | Auth | Description |
| :--- | :--- | :--- |
| `GET /health` | none | Liveness probe — always returns `{"status": "ok"}` |
| `GET /sse` | bearer | SSE transport entry point |
| `POST /messages` | bearer | MCP message endpoint |

## Example: run a program

```python
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client

program = {
    "steps": [
        {"id": "s1", "type": "tool", "tool": "my_tool", "input": {"query": "hello"}}
    ]
}

async def main():
    headers = {"Authorization": "Bearer your-secret-token"}
    async with sse_client("http://localhost:8080/sse", headers=headers) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool("run_program", {"program": program, "save_as": "demo"})
            print(result.content[0].text)

asyncio.run(main())
```

## Architecture

nano-vm-mcp acts as a **Stateful Gateway** layered over the `llm-nano-vm` Execution Kernel.
Every successful execution step is wrapped in a `GovernanceEnvelope` and persisted as an
immutable audit trail. The kernel and gateway are strictly isolated: the gateway never
touches execution logic, the kernel never touches persistence or policy.

```
MCP Client
  → nano-vm-mcp (Gateway)
      → GovernedRunProgramHandler   ← PolicySnapshot, CapabilityRef resolution
          → llm-nano-vm (Kernel)    ← deterministic FSM, ASTEngine, ProjectionLayer
      → GovernanceEnvelope store    ← SQLite WAL, append-only audit log
```

### GovernanceEnvelope

Each successful execution step produces a `GovernanceEnvelope` (frozen Pydantic model)
stored in the `governance_envelopes` table:

| Field | Type | Description |
| :--- | :--- | :--- |
| `execution_id` | `str` | Session / trace identifier |
| `step_id` | `int` | Step index within the execution |
| `policy_hash` | `str` | SHA-256 of the active `PolicySnapshot` |
| `canonical_snapshot_hash` | `str` | Merkle/delta hash of `CanonicalState` at this step |
| `payload` | `dict \| list` | Projected (sanitized) step output |

Envelopes are written only on `error=None` — they form a tamper-evident audit trail of
successful transitions, not of failures.

### CapabilityRef and Tombstoning

Sensitive values in `CanonicalState` are stored as `CapabilityRef` tokens
(`vault://secret/<id>`) rather than raw plaintext. On a GDPR erasure event (`E_gdpr_erase`),
the target ref is tombstoned (`is_tombstone=True`). All subsequent projections return the
constant `[REDACTED_TOMBSTONE]`, preserving the hash chain without exposing the erased value.

## Security

### Condition expressions

`run_program` accepts a full Program dict — including `condition` steps with
expression strings. As of v0.3.0, these are evaluated by the **ASTEngine** —
a deterministic sandboxed interpreter built into `llm-nano-vm`. `eval()` is
not used. The engine supports a fixed, safe operator set: `==`, `!=`, `>`,
`<`, `in`, `not in`, `and`, `or`, `not`, `contains`. Arbitrary Python
expressions outside this set are rejected with a parse error.

**Rules for safe use:**

- Condition logic must be authored by you, not generated from untrusted input at runtime.
- LLM output may appear as a *value being tested* (`'yes' in '$decision'`), never as the
  condition expression itself.
- If you expose this MCP server to untrusted clients, validate or allowlist condition
  expressions before passing them to `run_program`.
  
### Capability verification

`GovernedToolExecutor` intercepts every tool call and verifies the tool name against
the active `PolicySnapshot.tool_capabilities` before execution. Tools not listed in the
policy raise `CapabilityDeniedError` — they are never silently executed.

`ExecutionVM` additionally rejects any tool name not registered in its tool registry
with a `VMError`. These are two independent enforcement layers.

Avoid registering destructive or privileged tools (filesystem writes, shell exec,
database mutations) without an explicit access control layer in your tool implementation.

### SSE transport and auth

Set `NANO_VM_MCP_API_KEY` to enable bearer token authentication on the SSE transport.
The comparison is timing-safe (`secrets.compare_digest`). If the variable is unset,
a warning is logged to stderr and all requests are allowed — suitable for localhost only.

**Do not expose the SSE endpoint to the public internet without `NANO_VM_MCP_API_KEY` set**
or behind a reverse proxy with auth (nginx, Cloudflare Access, VPN).

---

## Roadmap

- [x] `run_program`, `get_trace`, `list_programs`, `get_program`, `delete_program` (v0.1.0)
- [x] stdio + SSE transports (v0.1.0)
- [x] SQLite WAL persistence (v0.1.0)
- [x] Bearer token auth for SSE — `NANO_VM_MCP_API_KEY`, timing-safe (v0.1.0)
- [x] `/health` liveness endpoint (v0.1.0)
- [x] Structured error responses + logging (v0.1.0)
- [x] `GovernanceEnvelope` — immutable audit trail per execution step (v0.3.0)
- [x] `GovernedRunProgramHandler` + `GovernedToolExecutor` + `CapabilityDeniedError` (v0.3.0)
- [x] `PolicySnapshot` CRUD — capability-gated tool execution (v0.3.0)
- [x] `CapabilityRef` + tombstoning — GDPR erasure with hash-chain preservation (v0.3.0)
- [x] ASTEngine in condition steps — `eval()` removed from production path (v0.3.0)
- [x] `governance_envelopes` table — append-only SQLite store with execution index (v0.3.0)
- [ ] `plan_and_run` — intent string → Planner → run (P7)
- [ ] `POST /mcp/session/{execution_id}/step` — full RFC step lifecycle with `vm.step()`
- [ ] `RemoteProjectionProvider` — IPC connector to Vault for JIT plaintext access
- [ ] Docker image to GHCR
