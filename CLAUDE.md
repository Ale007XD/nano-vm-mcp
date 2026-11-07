# CLAUDE.md — nano-vm-mcp (gateway)

MCP gateway over llm-nano-vm. Public repo: keep this file free of internal process
references.

## Commands
- `pytest -q` (164 tests at 0.4.7), `ruff check` (check only, never `--fix`),
  `mypy --ignore-missing-imports` must report 0 errors.
- Run from the repo root of THIS repo (`pwd`, `git rev-parse HEAD` before claiming CI status).

## Hard rules
- Pins: `mcp>=1.0.0,<2` (server written for the v1 low-level API), `litellm<1.98.0` in both
  extras, `llm-nano-vm>=0.8.10,<0.9`.
- Import `LiteLLMAdapter` from `nano_vm.adapters.litellm_adapter`, not `nano_vm.adapters`
  (lazy export is invisible to mypy).
- `vm_step` cancellation: on `CancelledError` in the resume branch delete the session row
  FIRST, then best-effort save `vm.crash_trace` (must not raise), then re-raise.
- Tools with DB/API side effects take a closure-injected session; commit happens at the
  caller's boundary, never inside a tool.
- A tool that is a terminal step with no downstream CONDITION must `raise` on failure
  (a returned sentinel never fails the trace).
- Tests that patch `AGENT_DEBUGGER_TOKEN/URL` use `patch.object(tools, ...)`.
- New public modules of llm-nano-vm must be added to the smoke-import list in `ci.yml`.
- Every fix ships with a test that fails on the old code.

## Working style
- Do not run `git commit`, `git push` or tag; show `git diff --stat` and stop.
