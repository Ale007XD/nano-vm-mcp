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

nano-vm-mcp is an **MCP gateway** that turns the [Model Context Protocol](https://modelcontextprotocol.io/) into a governance-bound execution environment. It wraps the `llm-nano-vm` execution kernel and exposes it to any MCP client — Claude Desktop, Claude Code, custom agents, or API callers — through stdio or SSE transport.

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

## Use with Claude Code Dynamic Workflows

nano-vm-mcp works as a governed execution backend for [Claude Code dynamic workflows](https://claude.com/blog/introducing-dynamic-workflows-in-claude-code). While Claude Code orchestrates subagents dynamically, nano-vm-mcp adds what native subagents lack: deterministic FSM execution, replayable traces, exactly-once semantics, and an append-only audit trail per workflow step.

### Why pair them

| | Claude Code Dynamic Workflows | + nano-vm-mcp |
| :--- | :---: | :---: |
| Parallel subagents | ✅ | ✅ |
| Dynamic orchestration | ✅ | ✅ |
| Deterministic step execution | ❌ | ✅ |
| Replayable audit trail per step | ❌ | ✅ |
| Inter-session idempotency | ❌ | ✅ |
| GDPR tombstoning | ❌ | ✅ |
| Capability enforcement (double gate) | ❌ | ✅ |

Use this combination when a workflow subagent must execute a governed process — payment pipeline, approval chain, compliance check — where correctness and auditability matter beyond the LLM layer.

### Setup

Install and start the server:

```bash
pip install nano-vm-mcp
nano-vm-mcp --transport stdio
```

Add to your Claude Code MCP configuration (project-level `.mcp.json` or `~/.claude/claude_desktop_config.json`):

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

### Example: governed payment step inside a workflow

A Claude Code subagent calls `run_program` to execute a payment pipeline with full governance:

```python
# Claude Code subagent calls this tool directly
result = await session.call_tool(
    "run_program",
    {
        "program": {
            "name": "payment_pipeline",
            "steps": [
                {"id": "validate",  "type": "tool", "tool": "validate_amount"},
                {"id": "reserve",   "type": "tool", "tool": "reserve_funds"},
                {"id": "capture",   "type": "tool", "tool": "capture_payment"},
                {"id": "receipt",   "type": "tool", "tool": "send_receipt",
                 "is_terminal": True},
            ]
        },
        "idempotency_key": "order-abc-123",  # exactly-once across retries and restarts
    }
)
# Returns: trace_id, status, step count, cost
# Every step produces a GovernanceEnvelope in SQLite — tamper-evident, append-only
```

The FSM kernel controls all state transitions. The subagent cannot skip steps, reorder execution, or bypass capability checks — regardless of what the LLM decides at the orchestration layer.

### Retrieve the audit trail

After execution, any agent or observer can retrieve the full trace:

```python
trace = await session.call_tool("get_trace", {"trace_id": result["trace_id"]})
# Returns: per-step status, duration_ms, usage, state_snapshots
```

Traces persist across sessions in SQLite WAL. `trace_id` is UUID4-stable for OTel propagation.

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

### PolicySnapshot and CapabilityRef — in depth

`PolicySnapshot` is a frozen Pydantic model created once per session. It carries the set of allowed tool names (`tool_capabilities`) and is hashed (SHA-256) before execution starts. Every `GovernanceEnvelope` records this hash — so any post-hoc modification of the policy is detectable.

```python
from nano_vm.contracts import PolicySnapshot, CapabilityRef

policy = PolicySnapshot(
    tool_capabilities={"reserve_funds", "capture_payment", "send_receipt"},
)
# policy.hash() → SHA-256 hex, stored in every GovernanceEnvelope.policy_hash
```

`CapabilityRef` wraps sensitive values as opaque tokens (`vault://secret/<id>`) rather than storing raw plaintext in `CanonicalState`. The token is resolved JIT during tool execution and never written to the audit log.

```python
ref = CapabilityRef(ref_id="card-4242", value="4242424242424242")
# Stored in state as: vault://secret/card-4242
# GovernanceEnvelope.payload contains the token, not the card number
```

### GDPR Tombstoning

On a GDPR erasure event (`E_gdpr_erase`):

- Target ref is tombstoned (`is_tombstone=True`)
- All subsequent projections return `[REDACTED_TOMBSTONE]`
- The `canonical_snapshot_hash` chain remains valid — forensic auditability is preserved
- The secret is permanently gone

```python
vm.erase(ref_id="card-4242")
# All future get_trace calls → payload contains "[REDACTED_TOMBSTONE]" for that field
# Hash chain remains intact — the erasure itself is auditable
```

### Execution traces

Every step also writes a TRACE projection to the `execution_traces` table — a sanitized snapshot of state visible to downstream observers (LLMs, dashboards) with sensitive values replaced by CapabilityRef tokens:

```python
steps = store.get_trace_steps(execution_id="exec-abc-123")
# [
#   {"step_index": 0, "step_id": "validate", "projected_json": "...", "canonical_hash": "..."},
#   {"step_index": 1, "step_id": "reserve",  "projected_json": "...", "canonical_hash": "..."},
# ]
```

---

## Determinism and LLM Steps

nano-vm-mcp provides two distinct guarantees:

**State determinism** — the FSM kernel guarantees execution order, no step skipping, and reproducible trace structure regardless of LLM output. The graph of transitions is fixed at program definition time. This is unconditional.

**Semantic determinism** — the text produced by an LLM step may differ across runs even at `temperature=0.0`. nano-vm does not guarantee semantic determinism and does not try to.

These are orthogonal concerns. The runtime enforces state determinism; you control semantic determinism through prompt engineering and `allowed_outputs`.

### Constraining LLM output at the runtime level

`allowed_outputs` (v0.8.0) validates the model's raw output against an explicit enum before it enters the FSM context — no prompt engineering required for enforcement:

```python
{
    "id": "classify",
    "type": "llm",
    "prompt": "Is this a valid refund request? Reply ONLY with: yes or no",
    "output_key": "decision",
    "allowed_outputs": ["yes", "no"],   # runtime enforcement — not a prompt hint
    "on_error": "skip",                 # output → "yes" (first element) on mismatch
}
```

If the model returns anything outside `["yes", "no"]`, the runtime handles it according to `on_error` — without propagating the invalid value to downstream steps or condition expressions.

Condition expressions are evaluated by the **ASTEngine** — a sandboxed interpreter with no access to Python builtins. LLM output can appear as a *value being tested*, never as the condition expression itself:

```python
# ✅ Safe — LLM output is a value, ASTEngine evaluates the expression
{"condition": "'yes' in '$decision'"}

# ❌ Never do this — condition expression must not come from LLM output
{"condition": user_supplied_expression}
```

---

## Performance

The FSM runtime introduces near-zero overhead. The bottleneck is always the LLM API or external I/O.

**Sequential execution** (single FSM instance): the FSM processes one step at a time per `execution_id`. This is a deliberate design choice — it makes traces deterministic and replayable.

**Parallel execution** across independent workflows: run multiple FSM instances with separate `execution_id` values. The SQLite WAL store handles concurrent writers without locking.

**`parallel` step type**: within a single FSM, `asyncio.gather` fans out independent sub-steps concurrently. Wall-clock time equals the slowest sub-step.

### Benchmarks (v0.7.3, Mock adapter, QEMU/KVM · Intel Xeon E5-2697A v4 · 2 cores · Python 3.12)

| Scenario | Mean TPS | p95 |
| :--- | ---: | ---: |
| Refund pipeline (sequential) | 2,300/s | 0.66 ms |
| MCP store round-trip | 3,000/s | 0.42 ms |
| GovernanceEnvelope write | 1,300/s | 171 ms |
| Parallel throughput (`asyncio.gather`) | 436/s | 542 ms |
| Replay equivalence | 1,300/s | 1.30 ms |
| Long-horizon (30-step program) | 30/s | 3,606 ms |

For high-throughput scenarios: fan out across multiple `execution_id` instances rather than serializing through a single FSM. Each instance is independent, lightweight, and SQLite WAL handles concurrent writes safely.

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

The kernel is MIT-licensed, independently versioned on PyPI (`llm-nano-vm`), and fully documented. Either layer can be used standalone or replaced — the boundary between them is a stable Python int
