What nano-vm-mcp Is

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
</p><p align="center">

nano-vm-mcp is a stateful MCP gateway built on top of [nano-vm](https://github.com/Ale007XD/nano_vm). It exposes deterministic execution workflows through the [Model Context Protocol](https://modelcontextprotocol.io/) ecosystem.

Unlike typical MCP servers that only expose stateless tools, nano-vm-mcp provides:

Capability	Typical MCP Server	nano-vm-mcp	
Tool execution	✅	✅	
Stateful workflows	❌	✅	
Deterministic FSM	❌	✅	
Replayable traces	❌	✅	
Suspend/resume	❌	✅	
Governance layer	❌	✅	
Capability enforcement	❌	✅	
Audit trail	partial	append-only	

Core principle: The gateway does not control execution logic — the deterministic FSM runtime does.

```
δ(S, E) → S'

Where:
  S  — current execution state
  E  — validated event
  S' — next deterministic state
```

---

Architecture

```
MCP Client
  → nano-vm-mcp (Gateway)
      → GovernedRunProgramHandler   ← PolicySnapshot, CapabilityRef resolution
          → llm-nano-vm (Kernel)    ← deterministic FSM, ASTEngine, ProjectionLayer
      → GovernanceEnvelope store    ← SQLite WAL, append-only audit log
```

The gateway and kernel are strictly isolated: the gateway never touches execution logic, the kernel never touches persistence or policy.

---

Install

```bash
pip install nano-vm-mcp
```

For programs with `llm` steps, install the LiteLLM extra:

```bash
pip install 'nano-vm-mcp[litellm]'
```

---

Quick Start

stdio transport — Claude Desktop / local MCP client

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

SSE transport — VPS / remote clients

```bash
NANO_VM_MCP_API_KEY=your-secret-token nano-vm-mcp --transport sse --port 8080
```

MCP client URL: `http://<<host>:8080/sse`

With auth header: `Authorization: Bearer your-secret-token`

Docker Compose

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

---

Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Variable	Default	Description	
`NANO_VM_MCP_DB`	`nano_vm_mcp.db`	SQLite WAL database path	
`NANO_VM_MCP_HOST`	`0.0.0.0`	SSE bind host	
`NANO_VM_MCP_PORT`	`8080`	SSE bind port	
`NANO_VM_MCP_API_KEY`	(unset)	Bearer token for SSE auth. If unset, all requests are allowed (warning logged)	
`NANO_VM_MCP_LLM_MODEL`	(unset)	LiteLLM model string for `llm` steps (e.g. `openrouter/meta-llama/llama-3.3-70b-instruct:free`)	

---

Endpoints

Path	Auth	Description	
`GET /health`	none	Liveness probe — always returns `{"status": "ok"}`	
`GET /sse`	bearer	SSE transport entry point	
`POST /messages`	bearer	MCP message endpoint	

---

MCP Tools

Tool	Description	
`run_program`	Execute a `Program` dict → returns `trace_id`, status, step count, cost	
`get_trace`	Retrieve full `Trace` JSON by `trace_id`	
`list_programs`	List saved programs (`id`, `name`, `created_at`)	
`get_program`	Retrieve saved `Program` JSON by `program_id`	
`delete_program`	Delete a program and all its traces	

---

Example: Run a Workflow

Without LLMs — Payment Pipeline

```python
program = {
    "name": "payment_flow",
    "steps": [
        {"id": "reserve", "type": "tool", "tool": "reserve_funds"},
        {"id": "capture", "type": "tool", "tool": "capture_payment"},
        {"id": "receipt", "type": "tool", "tool": "send_receipt"}
    ]
}
```

Execution properties: deterministic ordering, replayable trace, exactly-once semantics, append-only audit trail. No LLM involved.

Async Suspend / Resume

```python
async def wait_bank_transfer(**kwargs):
    return "PENDING"
```

FSM lifecycle: `RUNNING → SUSPENDED → RUNNING → SUCCESS`

This enables webhook orchestration, payment confirmation flows, human approvals, and long-running workflows.

Through MCP (SSE)

```python
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client

program = {
    "name": "demo",
    "steps": [
        {"id": "step1", "type": "tool", "tool": "hello_tool"}
    ]
}

async def main():
    headers = {"Authorization": "Bearer your-secret-token"}
    async with sse_client("http://localhost:8080/sse", headers=headers) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(
                "run_program",
                {"program": program, "save_as": "demo"}
            )
            print(result.content[0].text)

asyncio.run(main())
```

---

Deterministic Execution Guarantees

Guarantee	nano-vm-mcp	
Replayable traces	✅	
Deterministic transitions	✅	
Exactly-once execution	✅	
Suspend/resume	✅	
Auditability	✅	
Capability enforcement	✅	
Governance enforcement	✅	

---

State Model

Execution lifecycle:

```
CREATED
  ↓
RUNNING
  ↓
SUSPENDED
  ↓
RUNNING
  ↓
SUCCESS / FAILED
```

Terminal states are immutable.

---

Governance Layer

GovernanceEnvelope

Each successful execution step produces an immutable `GovernanceEnvelope`:

Field	Type	Description	
`execution_id`	`str`	Session / trace identifier	
`step_id`	`int`	Step index within the execution	
`policy_hash`	`str`	SHA-256 of the active `PolicySnapshot`	
`canonical_snapshot_hash`	`str`	Merkle/delta hash of `CanonicalState` at this step	
`payload`	`dict \| list`	Projected (sanitized) step output	

Envelopes are written only on `error=None` — they form a tamper-evident audit trail of successful transitions.

CapabilityRef and GDPR Tombstoning

Sensitive values in `CanonicalState` are stored as `CapabilityRef` tokens (`vault://secret/<id>`) rather than raw plaintext.

On erasure event (`E_gdpr_erase`):
- Target ref is tombstoned (`is_tombstone=True`)
- Projected values become `[REDACTED_TOMBSTONE]`
- Hash chain remains valid
- Secret disappears

---

Security

AST-safe Conditions

Condition expressions are evaluated through the deterministic ASTEngine — a sandboxed interpreter built into `llm-nano-vm`. `eval()` is never used.

Supported operators: `==`, `!=`, `>`, `<`, `in`, `not in`, `and`, `or`, `not`, `contains`

Rules for safe use:
- Condition logic must be authored by you, not generated from untrusted input at runtime.
- LLM output may appear as a value being tested (`'yes' in '$decision'`), never as the condition expression itself.
- If you expose this MCP server to untrusted clients, validate or allowlist condition expressions before passing them to `run_program`.

Capability Verification

Two independent enforcement layers:

Layer	Responsibility	
`ExecutionVM`	Registered tool validation — rejects unregistered tools with `VMError`	
`GovernedToolExecutor`	Policy capability validation — rejects unauthorized tools with `CapabilityDeniedError`	

Avoid registering destructive or privileged tools (filesystem writes, shell exec, database mutations) without an explicit access control layer in your tool implementation.

SSE Transport and Auth

Set `NANO_VM_MCP_API_KEY` to enable bearer token authentication. The comparison is timing-safe (`secrets.compare_digest`).

Do not expose the SSE endpoint to the public internet without `NANO_VM_MCP_API_KEY` set or behind a reverse proxy with auth (nginx, Cloudflare Access, VPN).

---

Observability

Every execution exposes:

```python
trace.trace_id
trace.status
trace.steps
trace.error
trace.state_snapshots
```

Execution becomes replayable and inspectable.

---

Relationship to nano-vm

Layer	Responsibility	
`nano-vm`	Deterministic execution kernel	
`nano-vm-mcp`	Stateful MCP gateway	

The gateway never owns transition logic. The FSM kernel does.

---

Use Cases

- Fintech orchestration
- Payment systems
- Governance-bound AI
- Enterprise automation
- Async approval workflows
- Webhook pipelines
- Deterministic AI execution
- MCP-native workflow systems

---

Roadmap

Status	Feature	Version	
✅	`run_program`, `get_trace`, `list_programs`, `get_program`, `delete_program`	v0.1.0	
✅	stdio + SSE transports	v0.1.0	
✅	SQLite WAL persistence	v0.1.0	
✅	Bearer token auth for SSE — `NANO_VM_MCP_API_KEY`, timing-safe	v0.1.0	
✅	`/health` liveness endpoint	v0.1.0	
✅	Structured error responses + logging	v0.1.0	
✅	`GovernanceEnvelope` — immutable audit trail per execution step	v0.3.0	
✅	`GovernedRunProgramHandler` + `GovernedToolExecutor` + `CapabilityDeniedError`	v0.3.0	
✅	`PolicySnapshot` CRUD — capability-gated tool execution	v0.3.0	
✅	`CapabilityRef` + tombstoning — GDPR erasure with hash-chain preservation	v0.3.0	
✅	ASTEngine in condition steps — `eval()` removed from production path	v0.3.0	
✅	`governance_envelopes` table — append-only SQLite store with execution index	v0.3.0	
✅	`get_trace` fix — `trace_id` now uses `trace.trace_id` from ExecutionVM (was `uuid4()`)	v0.3.1	
✅	Trace persistence: FK constraint removed, explicit cascade in `delete_program`	v0.3.1	
✅	`test_sprint4_trace_persistence.py` — TP-01–06 regression suite	v0.3.1	
⬜	`idempotency_store` — inter-session exactly-once guarantee	v0.4.0	
⬜	`plan_and_run` — intent string → Planner → run	P7	
⬜	`POST /mcp/session/{execution_id}/step` — full RFC step lifecycle with `vm.step()`	—	
⬜	`RemoteProjectionProvider` — IPC connector to Vault for JIT plaintext access	—	
⬜	Docker image to GHCR	—	

---

Contact

- nano-vm-mcp GitHub Repository: https://github.com/Ale007XD/nano-vm-mcp
- Kernel Runtime (nano-vm): https://github.com/Ale007XD/nano_vm
- PyPI: https://pypi.org/project/nano-vm-mcp/

---

License

MIT License
