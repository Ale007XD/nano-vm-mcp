"""
nano_vm_mcp.vmstep — vm.step() gateway primitive (sprint_5_mcp_vmstep).

Channel adapters (Telegram/Web/etc.) need a single call shaped like
(session_id, input) -> output that transparently handles both:
  - first turn:  no suspended cursor yet -> vm.run(program, context=input)
  - later turns: a suspended cursor exists -> resume_with_program(...)

without the adapter ever touching ExecutionVM, WebhookEvent, or
CursorRepository directly. This module is the gateway-level implementation
of that primitive, built entirely on top of llm-nano-vm's public API --
no changes to nano_vm core.

Two translation problems this module solves
---------------------------------------------
1. ExecutionVM.run() generates trace.trace_id internally on every call; it
   is never caller-supplied. CursorRepository.save()/load() are keyed by
   that trace_id. A channel adapter, however, addresses a conversation by
   its own session_id (e.g. a Telegram chat_id) -- a stable identifier that
   exists *before* any trace does. ProgramStore.vm_sessions is the bridge:
   session_id -> (trace_id, program_id). program_id is stored alongside
   trace_id because resume_with_program() requires a fresh Program object
   on every call -- the suspended cursor alone (step_id + state + trace) is
   not enough to reconstruct it.

2. ExecutionVM's default cursor_repository is an in-memory dict scoped to
   one ExecutionVM instance. A fresh instance is built on every vm_step()
   call (matching the existing run_program()/_build_vm() pattern), so an
   in-memory cursor_repository would lose its only suspended cursor the
   moment the call returns -- the very next vm_step() call for the same
   session would find nothing to resume. SQLiteCursorRepository below
   persists StateContext and Trace as JSON in ProgramStore.vm_cursors,
   surviving both across calls and across process restarts.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from nano_vm import ExecutionVM, Program, StateContext, Trace
from nano_vm.adapters import MockLLMAdapter
from nano_vm.vm import CursorRepository, WebhookEvent
from pydantic import ValidationError

from .store import ProgramStore
from .tools import _has_llm_steps


class SQLiteCursorRepository:
    """
    CursorRepository Protocol implementation backed by ProgramStore.vm_cursors.
    StateContext and Trace are stored as JSON (model_dump(mode="json")) and
    reconstructed via model_validate() on load -- both round-trip cleanly
    through Pydantic with no custom (de)serialization needed.
    """

    def __init__(self, store: ProgramStore) -> None:
        self._store = store

    async def save(
        self, trace_id: str, step_id: str, state: StateContext, trace: Trace
    ) -> None:
        self._store.save_vm_cursor(
            trace_id=trace_id,
            step_id=step_id,
            state_json=state.model_dump_json(),
            trace_json=trace.model_dump_json(),
        )

    async def load(self, trace_id: str) -> tuple[str, StateContext, Trace] | None:
        row = self._store.load_vm_cursor(trace_id)
        if row is None:
            return None
        state = StateContext.model_validate_json(row["state_json"])
        trace = Trace.model_validate_json(row["trace_json"])
        return row["step_id"], state, trace

    async def delete(self, trace_id: str) -> None:
        self._store.delete_vm_cursor(trace_id)


def _build_vm_with_cursor(
    program_data: dict[str, Any],
    cursor_repository: CursorRepository,
    tools: dict[str, Any] | None = None,
) -> ExecutionVM | str:
    """
    Same LLM-adapter-selection logic as tools._build_vm(), but accepts a
    cursor_repository so SQLiteCursorRepository is wired in from
    construction time, and a tools registry -- vm_step() runs in-process
    (the support-bot pilot imports nano_vm_mcp as a library, not over MCP
    transport), so TOOL-step functions are ordinary Python callables, not
    strings resolved server-side. Duplicates tools._build_vm()'s adapter
    selection intentionally -- that function returns a fully-built
    ExecutionVM with no way to inject cursor_repository/tools after the
    fact, and reaching into ExecutionVM's private attributes from here
    would couple this module to nano_vm_mcp internals it should not need
    to know about.
    """
    if not _has_llm_steps(program_data):
        return ExecutionVM(
            llm=MockLLMAdapter("noop"), tools=tools, cursor_repository=cursor_repository
        )

    model = os.getenv("NANO_VM_MCP_LLM_MODEL", "")
    if not model:
        return (
            "Program contains llm steps but NANO_VM_MCP_LLM_MODEL is not set. "
            "Set NANO_VM_MCP_LLM_MODEL (e.g. 'openrouter/llama-3.3-70b-instruct:free') "
            "and the corresponding API key in your environment."
        )
    try:
        from nano_vm.adapters import LiteLLMAdapter
    except ImportError:
        return (
            "LiteLLMAdapter is not available. Install it with: pip install 'nano-vm-mcp[litellm]'"
        )
    return ExecutionVM(
        llm=LiteLLMAdapter(model), tools=tools, cursor_repository=cursor_repository
    )


async def vm_step(
    store: ProgramStore,
    session_id: str,
    input: dict[str, Any] | None = None,
    program: dict[str, Any] | None = None,
    save_as: str = "",
    tools: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    MCP tool: (session_id, input) -> output for channel adapters.

    First call for a session: must include `program` (a Program dict).
    Behaves like run_program() -- executes program with `input` as context.
    If the trace suspends, the session_id -> (trace_id, program_id) mapping
    is recorded so the *next* vm_step() call for the same session_id resumes
    instead of starting over.

    Subsequent calls: `program` may be omitted -- the program_id stored
    against the session is used to reload it. `input` becomes the
    WebhookEvent payload, injected into state as $__webhook__ by
    resume_with_program(). source="OPERATOR" (channel adapter, not a raw
    webhook).

    `tools`: TOOL-step callables, keyed by name. vm_step() runs in-process
    (library import, not MCP transport) -- the caller's tool registry is
    passed directly on every call, same registry on both first-turn and
    resume-turn calls.

    Returns:
        {
          "session_id": str,
          "trace_id": str,
          "status": str,           # "SUCCESS" | "SUSPENDED" | "FAILED" | ...
          "suspended": bool,       # True if caller must call vm_step() again
          "output": Any,           # trace.final_output
          "error": str | None,
        }
    """
    payload: dict[str, Any] = input or {}
    existing_session = store.get_vm_session(session_id)
    cursor_repo = SQLiteCursorRepository(store)

    if existing_session is None:
        # First turn: program is required.
        if program is None:
            return {
                "session_id": session_id,
                "error": (
                    f"No existing session '{session_id}' and no 'program' provided. "
                    "First vm_step() call for a session must include the program."
                ),
            }
        try:
            program_obj = Program.model_validate(program)
        except ValidationError as exc:
            return {
                "session_id": session_id,
                "error": f"Invalid program: {exc.error_count()} validation error(s)",
                "detail": str(exc),
            }

        program_id = str(uuid.uuid4())
        store.save_program(program_id, save_as or session_id, program)

        vm = _build_vm_with_cursor(program, cursor_repo, tools)
        if isinstance(vm, str):
            return {"session_id": session_id, "error": vm}

        try:
            trace = await vm.run(program_obj, context=payload)
        except Exception as exc:
            return {
                "session_id": session_id,
                "error": f"Execution failed: {exc}",
                "program_id": program_id,
            }

        trace_id = str(trace.trace_id)
        status = str(trace.status).split(".")[-1]
        suspended = status == "SUSPENDED"

        if suspended:
            store.save_vm_session(session_id, trace_id, program_id)

        return {
            "session_id": session_id,
            "trace_id": trace_id,
            "status": status,
            "suspended": suspended,
            "output": trace.final_output if hasattr(trace, "final_output") else None,
            "error": None,
        }

    # Resume path: session already has a suspended trace.
    trace_id = existing_session["trace_id"]
    program_id = existing_session["program_id"]

    stored_program = store.get_program(program_id)
    if stored_program is None:
        return {
            "session_id": session_id,
            "error": f"Program '{program_id}' for session '{session_id}' not found in store.",
        }
    try:
        program_obj = Program.model_validate(stored_program)
    except ValidationError:
        return {
            "session_id": session_id,
            "error": "Stored program invalid — cannot resume.",
        }

    vm = _build_vm_with_cursor(stored_program, cursor_repo, tools)
    if isinstance(vm, str):
        return {"session_id": session_id, "error": vm}

    webhook = WebhookEvent(trace_id=trace_id, payload=payload, source="OPERATOR")

    try:
        trace = await vm.resume_with_program(webhook, program_obj)
    except Exception as exc:
        return {
            "session_id": session_id,
            "trace_id": trace_id,
            "error": f"Resume failed: {exc}",
        }

    status = str(trace.status).split(".")[-1]
    suspended = status == "SUSPENDED"

    if suspended:
        store.save_vm_session(session_id, str(trace.trace_id), program_id)
    else:
        store.delete_vm_session(session_id)

    return {
        "session_id": session_id,
        "trace_id": str(trace.trace_id),
        "status": status,
        "suspended": suspended,
        "output": trace.final_output if hasattr(trace, "final_output") else None,
        "error": None,
    }
