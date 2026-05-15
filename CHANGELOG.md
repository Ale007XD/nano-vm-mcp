# Changelog

All notable changes to `nano-vm-mcp` are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) · Versioning: [SemVer](https://semver.org/).

---

## [0.3.0] — 2026-05-11

### Added

- **`GovernanceEnvelope`** — frozen Pydantic model wrapping each successful execution step.
  Fields: `execution_id`, `step_id`, `policy_hash`, `canonical_snapshot_hash`, `payload`.
  Written only on `error=None`; forms a tamper-evident, append-only audit trail.

- **`governance_envelopes` table** in SQLite WAL store.
  Schema: `(id, execution_id, step_id, policy_hash, snapshot_hash, payload_json, created_at)` +
  index on `execution_id`. New store methods: `save_envelope`, `get_envelopes`, `delete_envelopes`.

- **`GovernedRunProgramHandler`** — replaces the bare `run_program` handler.
  Loads `PolicySnapshot`, resolves `CapabilityRef` tokens before execution,
  persists `GovernanceEnvelope` per step, returns envelope metadata in the MCP response.

- **`GovernedToolExecutor`** — intercepts every tool call, verifies the tool name against
  `PolicySnapshot.tool_capabilities` before dispatch. Raises `CapabilityDeniedError` on violation.

- **`CapabilityDeniedError`** — structured error type for policy violations; surfaced as a
  typed MCP error response (not a 500).

- **`CapabilityRef` support** — sensitive state values stored as `vault://secret/<id>` tokens.
  Gateway resolves refs via `RemoteProjectionProvider` stub (JIT plaintext, immediately dropped).

- **Tombstoning** — GDPR erasure event (`E_gdpr_erase`) sets `is_tombstone=True` on the target
  `CapabilityRef`. All subsequent projections return `[REDACTED_TOMBSTONE]`, preserving the
  `canonical_snapshot_hash` chain without exposing erased data.

- **`PolicySnapshot` CRUD** — create, read, and version policy snapshots. Each snapshot carries
  `policy_id`, `version`, `policy_hash` (SHA-256 of config), and `tool_capabilities` map.

- **ASTEngine for condition steps** — `eval()` removed from the production execution path.
  Condition expressions are parsed into a validated JSON AST and evaluated by a pure,
  sandboxed evaluator. Supported operators: `==`, `!=`, `>`, `<`, `in`, `not in`, `and`, `or`, `contains`.

- **`llm-nano-vm` dependency bumped to `0.7.0`** — picks up `erase()`, nested `CapabilityRef`
  traversal, `ProjectionLayer` API, and FSM invariant stress suite (BM-01–BM-12, 1,020,000 ops · 0 violations).

### Changed

- `handlers.py` — `GovernanceEnvelope` lives here (gateway contract, not core model).
  `_build_envelope()` computes `canonical_snapshot_hash` without importing `Trace` directly,
  avoiding gateway→core coupling.

- `tools.py` — `_extract_cost()` now performs a callable check before invoking
  `total_cost_usd()` to prevent `TypeError` when the method is called as a property.

- `store.py` — FK constraint (`traces → programs(id)`) enforced via `PRAGMA foreign_keys=ON`;
  `save_trace` requires a prior `save_program` call.

- README — new **Architecture** section documenting the Gateway / Kernel split,
  `GovernanceEnvelope` schema, `CapabilityRef` / tombstoning model, and updated Security section
  reflecting ASTEngine and `GovernedToolExecutor`.

### Security

- Condition expressions no longer use `eval()`. The ASTEngine provides full structural isolation:
  no builtins, no attribute access, no callable invocation outside the declared operator set.

- Tool execution is now double-gated: `GovernedToolExecutor` (policy layer) +
  `ExecutionVM` tool registry (kernel layer). Neither gate can be bypassed by LLM output.

---

## [0.2.0] — _internal / unreleased_

No public release. Sprint 2–3 work-in-progress: `ProjectionLayer` scaffolding,
`DeterministicSanitizer` stubs, `GovernanceEnvelope` design.

---

## [0.1.0] — initial release

### Added

- `run_program`, `get_trace`, `list_programs`, `get_program`, `delete_program` MCP tools.
- stdio and SSE transports.
- SQLite WAL persistence for programs and traces.
- Bearer token auth for SSE (`NANO_VM_MCP_API_KEY`, timing-safe via `secrets.compare_digest`).
- `/health` liveness endpoint (unauthenticated).
- Structured error responses + stderr logging.
- `.env.example` configuration template.
