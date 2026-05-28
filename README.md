<p align="center">
  <a href="https://github.com/Ale007XD/nano-vm-mcp/actions">
    <img src="https://github.com/Ale007XD/nano-vm-mcp/actions/workflows/ci.yml/badge.svg" alt="CI">
  </a>
  <a href="https://pypi.org/project/nano-vm-mcp/">
    <img src="https://img.shields.io/pypi/v/nano-vm-mcp" alt="PyPI">
  </a>
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
  <img src="https://img.shields.io/badge/MCP-compatible-purple" alt="MCP">
  <img src="https://img.shields.io/badge/FSM-deterministic-orange" alt="Deterministic FSM">
  <img src="https://img.shields.io/badge/audit-append--only-red" alt="Audit Trail">
  <img src="https://img.shields.io/badge/GDPR-tombstoning-blueviolet" alt="GDPR">
</p>

<p align="center">
  <strong>Stateful MCP gateway for deterministic LLM workflows.</strong><br>
  Governance-first. Replayable. Audit-complete.<br>
  Built on <a href="https://github.com/Ale007XD/nano_vm">llm-nano-vm</a> — the deterministic FSM execution kernel.
</p>

---

## What nano-vm-mcp Is

nano-vm-mcp is an **MCP gateway** that turns the [Model Context Protocol](https://modelcontextprotocol.io/) into a governance-bound execution environment. It wraps the `llm-nano-vm` execution kernel and exposes it to any MCP client — Claude Desktop, custom agents, or API callers — through stdio or SSE transport.

**Most MCP servers expose stateless tools.** nano-vm-mcp exposes stateful, governed, auditable workflows.

| Capability | Typical MCP Server | nano-vm-mcp |
| :--- | :---: | :---: |
| Tool execution | ✅ | ✅ |
| Stateful workflows | ❌ | ✅ |
| Deterministic FSM | ❌ | ✅ |
| Replayable traces | ❌ | ✅ |
| Suspend / resume | ❌ | ✅ |
| Capability enforcement | ❌ | ✅ |
| Append-only audit trail | ❌ | ✅ |
| GDPR tombstoning | ❌ | ✅ |
| Inter-session idempotency | ❌ | ✅ |

**Core invariant:** the gateway does not own execution logic — the FSM kernel does.

```
δ(S, E) → S'

  S  — current execution state
  E  — validated event
  S' — next deterministic state
```

---

## Architecture

```
MCP Client
  → nano-vm-mcp (Gateway)
      → GovernedRunProgramHandler   ← PolicySnapshot, idempotency_key, CapabilityRef resolution
          → llm-nano-vm (Kernel)    ← deterministic FSM, ASTEngine, ProjectionLayer
      → GovernanceEnvelope store    ← SQLite WAL, append-only audit log
      → idempotency_keys store      ← inter-session exactly-once guarantee
```

**Strict isolation:** the gateway never touches execution logic. The kernel never touches persistence or policy. Each layer has a single responsibility and cannot cross the boundary.

---

## Install

```bash
pip install nano-vm-mcp
```

For programs with `llm` steps:

```bash
pip install 'nano-vm-mcp[litellm]'
```

---

## MCP Tools

| Tool | Description |
| :--- | :--- |
| `run_program` | Execute a `Program` dict → returns `trace_id`, status, step count, cost |
| `get_trace` | Retrieve full `Trace` JSON by `trace_id` |
| `list_programs` | List saved programs (`id`, `name`, `created_at`) |
| `get_program` | Retrieve saved `Program` JSON by `program_id` |
| `delete_program` | Delete a program and all its traces |

---

## Quick Start

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
Auth header: `Authorization: Bearer your-secret-token`

### Docker Compose

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

---

## Endpoints

| Path | Auth | Description |
| :--- | :--- | :--- |
| `GET /health` | none | Liveness probe — always returns `{"status": "ok"}` |
| `GET /sse` | bearer | SSE transport entry point |
| `POST /messages` | bearer | MCP message endpoint |

---

## Example: Run a Workflow

### Payment pipeline — no LLM

```python
program = {
    "name": "payment_flow",
    "steps": [
        {"id": "reserve",  "type": "tool", "tool": "reserve_funds"},
        {"id": "capture",  "type": "tool", "tool": "capture_payment"},
        {"id": "receipt",  "type": "tool", "tool": "send_receipt"},
    ]
}
```

No LLM. The gateway still guarantees: deterministic ordering, replayable trace, exactly-once semantics, append-only audit trail.

### Async suspend / resume

Return the sentinel `"PENDING"` from any tool to suspend execution:

```python
async def wait_bank_transfer(**kwargs) -> str:
    await register_webhook(kwargs["order_id"])
    return "PENDING"   # FSM → SUSPENDED, cursor persisted
```

FSM lifecycle: `RUNNING → SUSPENDED → RUNNING → SUCCESS`

This enables: payment settlement, courier confirmation, approval workflows, webhook orchestration, human-in-the-loop.

> **Note:** `"PENDING"` is a reserved FSM sentinel. Use `"REQUIRES_ACTION"`, `"AWAITING_3DS"`, or any other string for domain-specific states.

### Through MCP (SSE)

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
                {
                    "program": program,
                    "save_as": "demo",
                    "idempotency_key": "order-abc-123",   # inter-session exactly-once
                }
            )
            print(result.content[0].text)

asyncio.run(main())
```

---

## Idempotency — Inter-session Exactly-Once

Pass `idempotency_key` to `run_program` to guarantee that a program executes at most once per key, even across process restarts:

```python
# First call — executes normally, result cached as "success"
result = await session.call_tool("run_program", {
    "program": program,
    "idempotency_key": "payment-order-xyz-001",
})

# Second call with same key — returns cached result immediately, no re-execution
result = await session.call_tool("run_program", {
    "program": program,
    "idempotency_key": "payment-order-xyz-001",
})
```

**Crash recovery:** if the process crashes after program start but before completion (`status=pending`), the next call with the same key overwrites the pending entry and re-executes. Once the result is written as `status=success`, it is immutable for that key.

This closes the inter-session duplicate risk that exists when a process restarts after creating a payment but before confirming it.

---

## Governance Layer

### GovernanceEnvelope

Each successful execution step produces an immutable `GovernanceEnvelope` stored in the `governance_envelopes` table:

| Field | Type | Description |
| :--- | :--- | :--- |
| `execution_id` | `str` | Session / trace identifier |
| `step_id` | `int` | Step index within the execution |
| `policy_hash` | `str` | SHA-256 of the active `PolicySnapshot` |
| `canonical_snapshot_hash` | `str` | Merkle/delta hash of `CanonicalState` at this step |
| `payload` | `dict \| list` | Projected (sanitized) step output |

Envelopes are written only on `error=None` — they form a tamper-evident, append-only audit trail of successful transitions only.

### CapabilityRef and GDPR Tombstoning

Sensitive values in `CanonicalState` are stored as `CapabilityRef` tokens (`vault://secret/<id>`) rather than raw plaintext.

On a GDPR erasure event (`E_gdpr_erase`):

- Target ref is tombstoned (`is_tombstone=True`)
- All subsequent projections return `[REDACTED_TOMBSTONE]`
- The `canonical_snapshot_hash` chain remains valid
- The secret is permanently gone

This preserves forensic auditability without retaining erased personal data.

---

## Security

### Condition expressions — ASTEngine

`run_program` accepts a full `Program` dict including `condition` steps with expression strings. These are evaluated by the **ASTEngine** — a deterministic sandboxed interpreter with no access to Python builtins, attribute access, or callable invocation.

Supported operators: `==`, `!=`, `>`, `<`, `in`, `not in`, `and`, `or`, `not`, `contains`, dotted-path `$var.field`.

`eval()` is not used anywhere in the production execution path.

**Rules for safe use:**

- Condition logic must be authored by you, not generated from untrusted input at runtime.
- LLM output may appear as a *value being tested* (`'yes' in '$decision'`), never as the condition expression itself.
- If you expose this server to untrusted clients, validate or allowlist condition expressions before passing them to `run_program`.

### Capability enforcement — double gate

Tool execution passes through two independent enforcement layers:

| Layer | Mechanism |
| :--- | :--- |
| `GovernedToolExecutor` | Verifies tool name against `PolicySnapshot.tool_capabilities`; raises `CapabilityDeniedError` on violation |
| `ExecutionVM` (kernel) | Rejects any tool name not registered in the tool registry with `VMError` |

Neither gate can be bypassed by LLM output. A tool not listed in the policy is never silently executed.

Avoid registering destructive or privileged tools (filesystem writes, shell exec, database mutations) without an explicit access control layer in your tool implementation.

### SSE transport and auth

Set `NANO_VM_MCP_API_KEY` to enable bearer token authentication. The comparison is timing-safe (`secrets.compare_digest`). If unset, a warning is logged and all requests are allowed — suitable for localhost only.

**Do not expose the SSE endpoint to the public internet without `NANO_VM_MCP_API_KEY` set** or behind a reverse proxy with auth (nginx, Cloudflare Access, VPN).

---

## Observability

Every execution exposes:

```python
trace.trace_id          # UUID4 — stable for OTel propagation
trace.status            # SUCCESS | FAILED | SUSPENDED | BUDGET_EXCEEDED | STALLED
trace.final_output
trace.steps             # per-step: step_id, status, duration_ms, usage
trace.state_snapshots   # list[(step_index, sha256_hex)]
```

Traces are persisted to SQLite and retrievable by `trace_id` across sessions via `get_trace`.

---

## Execution State Model

```
CREATED
  ↓
RUNNING ──── tool returns "PENDING" ──→ SUSPENDED
  │                                          │
  │                                    resume_with_program()
  │                                          │
  └──────────────────────────────────────────┘
  │
  ├── no more steps ──→ SUCCESS
  ├── tool error (on_error=fail) ──→ FAILED
  ├── max_steps / max_tokens exceeded ──→ BUDGET_EXCEEDED
  └── max_stalled_steps exceeded ──→ STALLED
```

Terminal states: `SUCCESS`, `FAILED`, `BUDGET_EXCEEDED`, `STALLED`. All are immutable.

---

## Relationship to nano-vm

| Layer | Responsibility |
| :--- | :--- |
| `llm-nano-vm` (kernel) | Deterministic FSM execution, ASTEngine, ProjectionLayer, step lifecycle |
| `nano-vm-mcp` (gateway) | MCP transport, persistence, governance, idempotency, capability enforcement |

The gateway never owns transition logic. The FSM kernel does.

---

## Roadmap

| Status | Feature | Version |
| :---: | :--- | :--- |
| ✅ | `run_program`, `get_trace`, `list_programs`, `get_program`, `delete_program` | v0.1.0 |
| ✅ | stdio + SSE transports | v0.1.0 |
| ✅ | SQLite WAL persistence | v0.1.0 |
| ✅ | Bearer token auth — `NANO_VM_MCP_API_KEY`, timing-safe | v0.1.0 |
| ✅ | `/health` liveness endpoint | v0.1.0 |
| ✅ | Structured error responses + logging | v0.1.0 |
| ✅ | `GovernanceEnvelope` — immutable audit trail per execution step | v0.3.0 |
| ✅ | `GovernedRunProgramHandler` + `GovernedToolExecutor` + `CapabilityDeniedError` | v0.3.0 |
| ✅ | `PolicySnapshot` CRUD — capability-gated tool execution | v0.3.0 |
| ✅ | `CapabilityRef` + tombstoning — GDPR erasure with hash-chain preservation | v0.3.0 |
| ✅ | ASTEngine in condition steps — `eval()` removed from production path | v0.3.0 |
| ✅ | `governance_envelopes` table — append-only SQLite store | v0.3.0 |
| ✅ | `trace_id` fix — uses `trace.trace_id` from `ExecutionVM` | v0.3.1 |
| ✅ | Trace persistence: FK constraint removed, explicit cascade in `delete_program` | v0.3.1 |
| ✅ | `idempotency_store` — inter-session exactly-once guarantee | v0.4.0 |
| ✅ | `build_chain()` → `GovernedRunProgramHandler` — capability gate always active | v0.4.0 |
| ✅ | TRACE projection logging to SQLite — `execution_traces` table + `save_trace_step`/`get_trace_steps` | v0.4.1 |
| ⬜ | `PROGRAM_IPN_HANDLER` DSL — webhook confirmation path | — |
| ⬜ | `GovernedToolExecutor` circuit breaker — degradation isolation | — |
| ⬜ | `POST /mcp/session/{execution_id}/step` — full RFC step lifecycle | — |
| ⬜ | `RemoteProjectionProvider` — IPC connector to Vault for JIT plaintext access | — |
| ⬜ | `plan_and_run` — intent string → Planner → run | — |
| ⬜ | Docker image to GHCR | — |

---

## License

[MIT License](LICENSE)

---

## Contact

- **Kernel runtime:** [nano-vm](https://github.com/Ale007XD/nano_vm)  
- **Telegram:** [@ale007xd](https://t.me/ale007xd)  
- **X:** [@ale007xd](https://x.com/ale007xd)
