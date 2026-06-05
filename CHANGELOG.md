# Changelog

All notable changes to `nano-vm-mcp` are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) · Versioning: [SemVer](https://semver.org/).

---

## [0.4.3] — 2026-06-04

### Added
- `transition_stats` table: `(program_name, model_id, from_step, to_step, count)`
  with `UNIQUE(...) ON CONFLICT DO UPDATE count+1`
- `store.upsert_transition(program_name, from_step, to_step, model_id='__none__')` 
- `store.get_transitions(program_name, model_id=None) → list[dict]`
- `tools.py`: вызов `upsert_transition` после `vm.run()` для каждой пары `(steps[i], steps[i+1])`
- `model_id` из env `NANO_VM_MCP_LLM_MODEL` или `'__none__'`

### CI
- `pyproject.toml`: `llm-nano-vm` явно в `[dev]` extras
- `pyproject.toml`: `litellm` в `[dev]` extras  
- `ci.yml`: `cache-dependency-path: pyproject.toml`
- `ci.yml`: `pip install --upgrade` в install шаге
- `ci.yml`: smoke-import шаг в lint job
- `ci.yml`: verify wheel шаг в test job

### Tests
- TE-01..14: transition_stats CRUD, upsert idempotency, count increment, model_id filter
- CI: 115/115 PASS

## v0.4.2 (2026-05-31)

### Fixed
- `execution_traces`: added `UNIQUE INDEX (execution_id, step_index)` — prevents duplicate
  step entries during retry storms (`INSERT OR IGNORE` in `save_trace_step`).
  Existing databases gain the index automatically on first startup via
  `CREATE UNIQUE INDEX IF NOT EXISTS`.

## [0.4.1] — 2026-05-28

### Added

- **`execution_traces` table** in SQLite WAL store.
  Schema: `(id INTEGER PK AUTOINCREMENT, execution_id TEXT, step_index INTEGER, step_id TEXT,
  projected_json TEXT, canonical_hash TEXT, created_at TEXT)` + index on `execution_id`.
  New store methods: `save_trace_step`, `get_trace_steps`.

- **TRACE projection logging in `GovernedRunProgramHandler._try_handle`** — after each
  successful `run_program`, a TRACE-projected record is written to `execution_traces`.
  The record carries `trace_id`, `status`, `steps`, `cost`, `projection_target="TRACE"`,
  and `canonical_snapshot_hash` from the `GovernanceEnvelope`. Written only on `error=None`,
  same invariant as `governance_envelopes`. This closes the gap where the audit trail existed
  only in-memory until process restart.

- **`tests/test_sprint_trace_logging.py`** — 6 tests (TL-01–TL-06).
  Covers: `save_trace_step` / `get_trace_steps` round-trip, empty result for unknown
  `execution_id`, multi-step sort order by `step_index`, positive `rowid` guarantee,
  `GovernedRunProgramHandler` records trace step on success, no trace step on error.

---

## [0.4.0] — 2026-05-25

### Added

- **`idempotency_keys` table** in SQLite WAL store.
  Schema: `(idempotency_key TEXT PK, status TEXT, result_json TEXT, created_at TEXT, updated_at TEXT)`.
  New store methods: `save_idempotency_key`, `get_idempotency_key`, `delete_idempotency_key`.
  Status lifecycle: `pending` → `success` (upserted on completion).

- **Idempotency flow in `GovernedRunProgramHandler._try_handle`** — before execution the
  handler checks for a cached result by `idempotency_key`. If found with `status=success`,
  the cached MCP response is returned immediately without re-running the program.
  If `status=pending` (crash mid-flight), the entry is overwritten and execution proceeds.
  On success, the result is upserted with `status=success`.

- **`idempotency_key` parameter in `tools.run_program`** — optional string. When provided,
  the full idempotency flow is activated. When omitted, behaviour is unchanged (no-op).

- **`build_chain()` now uses `GovernedRunProgramHandler`** — previously `build_chain` wired
  `RunProgramHandler` directly, bypassing the capability gate. Fixed: `GovernedRunProgramHandler`
  (with `policy=None`) is now the head of the chain.

- **`tests/test_sprint4_idempotency.py`** — 10 tests (IP-01–IP-10) using `asyncio.run`
  wrappers (no `pytest-asyncio` dependency). Covers: cache hit on second call, `pending`
  crash recovery, idempotency key absent (passthrough), distinct keys execute independently,
  `status=pending` upsert, FAILED trace does not cache, `delete_idempotency_key` cleanup,
  concurrent-safe sequential calls, `GovernedRunProgramHandler` integration, store round-trip.

### Fixed

- **`build_chain()` capability gate bypass** — `RunProgramHandler` was used instead of
  `GovernedRunProgramHandler`, meaning tool capability enforcement was silently skipped for
  all MCP `run_program` calls. Fixed: `GovernedRunProgramHandler(policy=None)` is now
  always the entry point.

---

## [0.3.1] — 2026-05-24

### Fixed

- **`trace_id` bug in `tools.py`** — `run_program` was generating a new `uuid.uuid4()` for
  `trace_id` instead of using `trace.trace_id` from `ExecutionVM`. As a result, `get_trace`
  via MCP always returned `{"error": "not found"}` because the stored ID never matched the
  returned ID. Fixed: `trace_id = str(trace.trace_id) if hasattr(trace, "trace_id") else str(uuid.uuid4())`.

- **`FOREIGN KEY` constraint removed from `traces` table** — the FK
  `traces(program_id) REFERENCES programs(id)` caused `IntegrityError` when `save_trace`
  was called before a corresponding `save_program` (e.g. on FAILED executions without
  `save_as`). Traces are now stored independently of programs.
  Cascade behaviour preserved explicitly: `delete_program` now issues
  `DELETE FROM traces WHERE program_id = ?` before the program delete.

- **Duplicate `save_trace` call removed from `handlers.py`** — `GovernedRunProgramHandler`
  was calling `store.save_trace` in addition to `tools.run_program` which already owns that
  responsibility. The duplicate call is removed; single ownership restored to `tools.py`.

### Added

- **`tests/test_sprint4_trace_persistence.py`** — 6 regression tests (TP-01–06).
  Covers: `save_trace` / `get_trace` round-trip, missing trace returns `None`,
  `GovernedRunProgramHandler` delegates to `_tools.run_program`, MCP `get_trace` tool,
  `INSERT OR REPLACE` update semantics, `FAILED` trace persisted correctly.

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
