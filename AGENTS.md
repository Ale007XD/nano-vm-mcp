# AGENTS.md — nano-vm-mcp (gateway)

MCP gateway over llm-nano-vm. This file is for coding agents; keep it short.

## Commands
- `pytest -q`, `ruff check` (check only, never `--fix`), `mypy --ignore-missing-imports` (0 errors).
- Never run `git commit`, `git push`, `git tag`, `git reset`. Run from THIS repo root.

## Hard rules
- Pins: `mcp>=1.0.0,<2`, `litellm<1.98.0` (both extras), `llm-nano-vm>=0.8.10,<0.9`.
- Import `LiteLLMAdapter` from `nano_vm.adapters.litellm_adapter`, not `nano_vm.adapters`.
- `vm_step` on `CancelledError` in the resume branch: delete the session row FIRST, then
  best-effort save `vm.crash_trace` (must not raise), then re-raise.
- Tools with DB/API side effects take a closure-injected session; commit at the caller boundary.
- A terminal tool step with no downstream CONDITION must `raise` on failure.
- Tests patching `AGENT_DEBUGGER_TOKEN/URL` use `patch.object(tools, ...)`.
- Every change ships with a test that is RED on the old code. Never weaken or skip tests.

## How to work (mandatory protocol — you are a small model, follow it literally)
1. Read the task brief in `.briefs/` fully. Do exactly one numbered step at a time.
2. Before using ANY class/function/attribute name, `grep` the source to confirm it exists and
   read its definition. Never invent APIs, signatures or file paths.
3. One file per step. Do not rewrite whole files; make small anchored edits.
4. After each step run the narrowest test, then paste the RAW command and RAW output.
5. If something is unclear, a command fails twice, or you are rate-limited: STOP and report
   what you know and what you do not know. Do not guess.
6. Finish with `tools/gate.sh`, `git diff --stat`, and a report that separates CONFIRMED
   (you ran it, output shown) from NOT CONFIRMED. Do not claim CI status; you cannot see it.
