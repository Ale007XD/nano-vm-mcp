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
  <strong>Governed Agent Execution gateway for LLM workflows.</strong><br>
  Enforcement-first. Replayable. Audit-complete.<br>
  Built on <a href="https://github.com/Ale007XD/nano_vm">llm-nano-vm</a> — the deterministic FSM execution kernel.
</p>

<p align="center">
  <em>Claude Code decides what to do. nano-vm-mcp decides how execution is allowed to proceed.</em>
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
| LLM output enforcement | ❌ | ✅ |
| Capability enforcement (double gate) | ❌ | ✅ |
| Append-only audit trail | ❌ | ✅ |
| GDPR tombstoning | ❌ | ✅ |
| Evaluator blindness by design | ❌ | ✅ |
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
MCP Client / Claude Code
        ↓
  nano-vm-mcp (Gateway)    ← decides how execution is allowed to proceed
      → GovernedRunProgramHandler   ← PolicySnapshot, idempotency_key, CapabilityRef
          → llm-nano-vm (Kernel)    ← deterministic FSM, ASTEngine, ProjectionLayer
      → GovernanceEnvelope store    ← SQLite WAL, append-only audit log
      → idempotency_keys store      ← idempotent re-execution across restarts
        ↓
  deterministic FSM        ← guarantees correctness
        ↓
  GovernanceEnvelope       ← proves it happened
```

**Strict isolation:** the gateway never touches execution logic. The kernel never touches persistence or policy. Each layer has a single responsibility and cannot cross the boundary.

---

## Install

```bash
pip install nano-vm-mcp
pip install 'nano-vm-mcp[litellm]'   # for llm steps
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

`claude_desktop_config.json` or `.mcp.json`:

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

## Claude Code Dynamic Workflows

**Claude Code decides what to do. nano-vm-mcp decides how execution is allowed to proceed.**

Claude Code Dynamic Workflows give you parallel subagents and dynamic orchestration. They don't give you deterministic step execution, replayable audit trails per step, or idempotent re-execution across restarts. nano-vm-mcp closes exactly that gap.

```
Claude Code          ← decides what to do
    ↓
nano-vm-mcp          ← enforces how execution proceeds
    ↓
deterministic FSM    ← guarantees correctness
    ↓
GovernanceEnvelope   ← proves it happened
```

| | Claude Code Dynamic Workflows | + nano-vm-mcp |
| :--- | :---: | :---: |
| Parallel subagents | ✅ | ✅ |
| Dynamic orchestration | ✅ | ✅ |
| Deterministic step execution | ❌ | ✅ |
| Replayable audit trail per step | ❌ | ✅ |
| LLM output enforcement | ❌ | ✅ |
| Inter-session idempotency | ❌ | ✅ |
| GDPR tombstoning | ❌ | ✅ |
| Evaluator blindness | ❌ | ✅ |

Use this combination when a workflow subagent must execute a governed process — payment pipeline, approval chain, compliance check — where correctness and auditability matter beyond the LLM layer.

### Example: governed payment step inside a Claude Code workflow

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
        "idempotency_key": "order-abc-123",
    }
)
# Returns: trace_id, status, step count, cost
# Every step: GovernanceEnvelope in SQLite — tamper-evident, append-only
```

The subagent cannot skip steps, reorder execution, or bypass capability checks — regardless of what the LLM decides at the orchestration layer.

### Retrieve the audit trail

```python
trace = await session.call_tool("get_trace", {"trace_id": result["trace_id"]})
# Returns: per-step status, duration_ms, usage, state_snapshots
```

Traces persist across sessions in SQLite WAL. `trace_id` is UUID4-stable for OTel propagation.

---

## Idempotency — Inter-session Re-execution Safety

Pass `idempotency_key` to `run_program` to guarantee that a program executes at most once per key, even across process restarts:

```python
# First call — executes normally, result cached
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

**Note on "exactly-once":** the FSM guarantees idempotent re-execution — the same key never triggers a second run after success. External side effects (payment capture, webhook delivery) are only as idempotent as the tools you register. This is the same contract Temporal and Cadence operate under.

---

## Governance Layer

### GovernanceEnvelope

Each successful execution step produces an immutable `GovernanceEnvelope` stored in the `governance_envelopes` table. Envelopes are written only on `error=None` — they form a tamper-evident, append-only audit trail of successful transitions only.

| Field | Type | Description |
| :--- | :--- | :--- |
| `execution_id` | `str` | Session / trace identifier |
| `step_id` | `int` | Step index within the execution |
| `policy_hash` | `str` | SHA-256 of the active `PolicySnapshot` |
| `canonical_snapshot_hash` | `str` | Merkle/delta hash of `CanonicalState` at this step |
| `payload` | `dict \| list` | Projected (sanitized) step output |

### PolicySnapshot and CapabilityRef

`PolicySnapshot` is a frozen Pydantic model created once per session. It carries the set of allowed tool names and is hashed (SHA-256) before execution starts. Every `GovernanceEnvelope` records this hash — post-hoc modification of the policy is detectable.

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

On a GDPR erasure event:

- Target ref is tombstoned (`is_tombstone=True`)
- All subsequent projections return `[REDACTED_TOMBSTONE]`
- The `canonical_snapshot_hash` chain remains valid — forensic auditability is preserved
- The secret is permanently gone

```python
vm.erase(ref_id="card-4242")
# Hash chain remains intact — the erasure itself is auditable
```

### Execution Traces

Every step writes a TRACE projection to the `execution_traces` table — a sanitized snapshot of state with sensitive values replaced by CapabilityRef tokens, protected by a unique index on `(execution_id, step_index)`:

```python
steps = store.get_trace_steps(execution_id="exec-abc-123")
# [
#   {"step_index": 0, "step_id": "validate", "projected_json": "...", "canonical_hash": "..."},
#   {"step_index": 1, "step_id": "reserve",  "projected_json": "...", "canonical_hash": "..."},
# ]
```

---

## Evaluator Blindness by Design

Benchmark research across 10 frontier models found that every model changes behavior when it detects it's being monitored. The best model scored 84% on "acts the same whether watched or not."

The `ProjectionLayer` gives the LLM only a `target=LLM` projection of state. Governance metadata — `GovernanceEnvelope`, `canonical_hash`, `policy_hash`, retry counters — never reaches the prompt. The model cannot observe its own audit trail.

**Evaluator blindness is structural, not configured.** It is a consequence of the strict isolation between the kernel and gateway layers.

---

## Determinism and LLM Steps

**State determinism** — the FSM kernel guarantees execution order, no step skipping, and reproducible trace structure regardless of LLM output. The graph of transitions is fixed at program definition time. This is unconditional.

**Semantic determinism** — the text produced by an LLM step may differ across runs even at `temperature=0.0`. nano-vm does not guarantee semantic determinism and does not try to.

These are orthogonal concerns. The runtime enforces state determinism; you control semantic determinism through prompt engineering and `allowed_outputs`.

### LLM output enforcement at the runtime level

`allowed_outputs` (v0.8.0) validates the model's raw output against an explicit enum *before* it enters the FSM context. This isn't a prompt hint — it's a runtime gate.

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

---

## Security

### ASTEngine — sandboxed condition evaluation

Conditions are evaluated by the **ASTEngine** — a deterministic sandboxed interpreter with no access to Python builtins, attribute access, or callable invocation. `eval()` is not used anywhere in the production execution path.

**Rules for safe use:**
- Condition logic must be authored by you, not generated from untrusted input at runtime.
- LLM output may appear as a *value being tested* (`'yes' in '$decision'`), never as the condition expression itself.

### Capability enforcement — double gate

Tool execution passes through two independent enforcement layers:

| Layer | Mechanism |
| :--- | :--- |
| `GovernedToolExecutor` | Verifies tool name against `PolicySnapshot.tool_capabilities`; raises `CapabilityDeniedError` on violation |
| `ExecutionVM` (kernel) | Rejects any tool name not registered in the tool registry with `VMError` |

Neither gate can be bypassed by LLM output.

### SSE transport and auth

Set `NANO_VM_MCP_API_KEY` to enable bearer token authentication (`secrets.compare_digest` — timing-safe). If unset, a warning is logged and all requests are allowed — suitable for localhost only.

**Do not expose the SSE endpoint to the public internet without `NANO_VM_MCP_API_KEY` set.**

---

## Configuration

| Variable | Default | Description |
| :--- | :--- | :--- |
| `NANO_VM_MCP_DB` | `nano_vm_mcp.db` | SQLite WAL database path |
| `NANO_VM_MCP_HOST` | `0.0.0.0` | SSE bind host |
| `NANO_VM_MCP_PORT` | `8080` | SSE bind port |
| `NANO_VM_MCP_API_KEY` | _(unset)_ | Bearer token for SSE auth |
| `NANO_VM_MCP_LLM_MODEL` | _(unset)_ | LiteLLM model string for `llm` steps |

---

## Endpoints

| Path | Auth | Description |
| :--- | :--- | :--- |
| `GET /health` | none | Liveness probe — always returns `{"status": "ok"}` |
| `GET /sse` | bearer | SSE transport entry point |
| `POST /messages` | bearer | MCP message endpoint |

---

## Performance

The FSM runtime introduces near-zero overhead. The bottleneck is always the LLM API or external I/O.

**Sequential execution** (single FSM instance): one step at a time per `execution_id` — deliberate design choice, makes traces deterministic and replayable.

**Parallel execution** across independent workflows: fan out across multiple `execution_id` instances. SQLite WAL handles concurrent writers without locking.

### Benchmarks (v0.7.3, Mock adapter, QEMU/KVM · Intel Xeon E5-2697A v4 · 2 cores · Python 3.12)

| Scenario | Mean TPS | p95 |
| :--- | ---: | ---: |
| Refund pipeline (sequential) | 2,300/s | 0.66 ms |
| MCP store round-trip | 3,000/s | 0.42 ms |
| GovernanceEnvelope write | 1,300/s | 171 ms |
| Parallel throughput (`asyncio.gather`) | 436/s | 542 ms |
| Replay equivalence | 1,300/s | 1.30 ms |
| Long-horizon (30-step program) | 30/s | 3,606 ms |

---

## Observability

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

## Relationship to llm-nano-vm

| Layer | Responsibility |
| :--- | :--- |
| `llm-nano-vm` (kernel) | Deterministic FSM execution, ASTEngine, ProjectionLayer, step lifecycle |
| `nano-vm-mcp` (gateway) | MCP transport, persistence, governance, idempotency, capability enforcement |

The gateway never owns transition logic. The FSM kernel does.

The kernel is MIT-licensed, independently versioned on PyPI (`llm-nano-vm`), and fully documented. Either layer can be used standalone or replaced — the boundary between them is a stable Python interface.

---

## Contact & Support

**Author:** [@ale007xd](https://t.me/ale007xd) on Telegram · [@ale007xd](https://x.com/ale007xd) on X

[![USDT (TON)](https://img.shields.io/badge/USDT%20(TON)-2ea2cc?style=flat-square)](https://tonviewer.com/UQCakyytrEGBikOi3eYMpveGHXDB1-fd6lcuQC9VvKqMrI-9)

**USDT (TON):** `UQCakyytrEGBikOi3eYMpveGHXDB1-fd6lcuQC9VvKqMrI-9`

---

## License

[MIT License](LICENSE).
