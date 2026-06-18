"""nano_vm_mcp.tools — MCP tool implementations."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from nano_vm import ExecutionVM, Program
from nano_vm.adapters import MockLLMAdapter
from pydantic import ValidationError

from .store import ProgramStore

logger = logging.getLogger(__name__)

AGENT_DEBUGGER_URL = os.getenv(
    "AGENT_DEBUGGER_URL",
    "https://agent-debugger-production.up.railway.app",
)
AGENT_DEBUGGER_TOKEN = os.getenv("AGENT_DEBUGGER_TOKEN", "")


def _has_llm_steps(program_data: dict[str, Any]) -> bool:
    """Return True if any step (including parallel sub-steps) requires an LLM."""

    def _scan(steps: list[dict[str, Any]]) -> bool:
        for step in steps:
            if step.get("type") == "llm":
                return True
            if step.get("type") == "parallel":
                if _scan(step.get("parallel_steps", [])):
                    return True
        return False

    return _scan(program_data.get("steps", []))


def _build_vm(program_data: dict[str, Any]) -> ExecutionVM | str:
    """
    Build ExecutionVM with the appropriate LLM adapter.

    - tool/condition/parallel-only programs: MockLLMAdapter("noop") — no API key needed.
    - programs with llm steps: LiteLLMAdapter from NANO_VM_MCP_LLM_MODEL env var.

    Returns ExecutionVM on success, or a str error message if llm steps are present
    but NANO_VM_MCP_LLM_MODEL is not configured.
    """
    if not _has_llm_steps(program_data):
        return ExecutionVM(llm=MockLLMAdapter("noop"))

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
    return ExecutionVM(llm=LiteLLMAdapter(model))


def _extract_cost(trace: Any) -> float:
    """
    Извлекает стоимость из Trace совместимым способом.

    total_cost_usd — метод (callable), не property. Нужно вызывать.
    Fallback на total_cost (атрибут, старые версии).
    """
    if hasattr(trace, "total_cost_usd"):
        val = trace.total_cost_usd
        # Может быть методом или property в зависимости от версии nano_vm
        if callable(val):
            val = val()
        return float(val or 0.0)
    if hasattr(trace, "total_cost"):
        return float(trace.total_cost or 0.0)
    return 0.0


def _build_debugger_payload(trace_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Build Agent Debugger /analyze payload from stored trace dict.

    Maps nano-vm Trace fields to the agreed request schema.
    FAIL:<reason> sentinels are intentional FSM outputs — not tool errors.
    """
    steps = trace_dict.get("steps", [])
    mapped_steps = []
    for i, s in enumerate(steps):
        mapped_steps.append(
            {
                "step_id": s.get("step_id", f"step_{i}"),
                "type": s.get("type", "tool"),
                "status": str(s.get("status", "UNKNOWN")).split(".")[-1],
                "output": str(s.get("output", "")),
                "retries": s.get("retry_count", 0),
                "duration_ms": s.get("duration_ms", 0),
            }
        )

    trace_id = trace_dict.get("trace_id", "")
    raw_status = str(trace_dict.get("status", "FAILED"))
    status = raw_status.split(".")[-1] if "." in raw_status else raw_status
    return {
        "trace": {
            "trace_id": trace_id,
            "program_name": trace_dict.get("program_name", ""),
            "status": status,
            "steps": mapped_steps,
            "final_step": mapped_steps[-1]["step_id"] if mapped_steps else "",
            "escalations": 0,
            "blocked_actions": 0,
            "transition_entropy": trace_dict.get("transition_entropy", 0.0),
            "rollback_density": trace_dict.get("rollback_density", 0.0),
            "tool_churn_rate": trace_dict.get("tool_churn_rate", 0.0),
        },
    }


async def call_agent_debugger(trace_dict: dict[str, Any]) -> dict[str, Any]:
    """
    POST trace to Agent Debugger /analyze endpoint.

    Returns diagnostic dict or {"error": reason} if unavailable.
    Requires AGENT_DEBUGGER_TOKEN env var.
    """
    if not _HTTPX_AVAILABLE:
        return {"error": "httpx not installed — pip install httpx"}
    if not AGENT_DEBUGGER_TOKEN:
        return {"error": "AGENT_DEBUGGER_TOKEN not set"}

    payload = _build_debugger_payload(trace_dict)
    url = f"{AGENT_DEBUGGER_URL.rstrip('/')}/analyze"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {AGENT_DEBUGGER_TOKEN}"},
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
    except httpx.HTTPStatusError as exc:
        return {"error": f"Agent Debugger HTTP {exc.response.status_code}: {exc.response.text}"}
    except Exception as exc:
        return {"error": f"Agent Debugger unreachable: {exc}"}


async def debug_trace(store: ProgramStore, trace_id: str) -> dict[str, Any]:
    """
    MCP tool: retrieve trace by ID and run Agent Debugger diagnostics.

    Returns combined result: trace metadata + diagnostic from Agent Debugger.
    """
    trace_dict = store.get_trace(trace_id)
    if trace_dict is None:
        return {"error": f"Trace '{trace_id}' not found"}

    diagnostic = await call_agent_debugger(trace_dict)
    return {
        "trace_id": trace_id,
        "status": trace_dict.get("status"),
        "diagnostic": diagnostic,
    }


async def run_program(
    store: ProgramStore,
    program_data: dict[str, Any],
    save_as: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    """
    Validate and execute a Program dict.

    Args:
        store: ProgramStore instance.
        program_data: Raw dict conforming to nano_vm.Program schema.
        save_as: Optional name to persist the program in the store.
        idempotency_key: Optional key for exactly-once execution guarantee (v0.4.0).

    Returns:
        {"trace_id": str, "program_id": str, "status": str,
         "steps": int, "cost": float, "error": str | None}
    """
    try:
        program = Program.model_validate(program_data)
    except ValidationError as exc:
        return {
            "error": f"Invalid program: {exc.error_count()} validation error(s)",
            "detail": str(exc),
        }

    program_id = str(uuid.uuid4())
    if save_as:
        store.save_program(program_id, save_as, program_data)

    vm_or_err = _build_vm(program_data)
    if isinstance(vm_or_err, str):
        return {"error": vm_or_err}

    try:
        trace = await vm_or_err.run(program)
    except Exception as exc:
        logger.exception("vm_run_failed program_id=%s", program_id)
        return {"error": f"Execution failed: {exc}", "program_id": program_id}

    # Use trace.trace_id (UUID4 assigned by ExecutionVM, OTel-ready)
    # Do NOT generate a new uuid4 — get_trace by trace_id would never match.
    trace_id = str(trace.trace_id) if hasattr(trace, "trace_id") else str(uuid.uuid4())
    trace_dict = trace.model_dump(mode="json") if hasattr(trace, "model_dump") else vars(trace)
    cost = _extract_cost(trace)

    if not save_as:
        store.save_program(program_id, "", program_data)

    store.save_trace(
        trace_id=trace_id,
        program_id=program_id,
        status=str(trace.status),
        steps_count=len(trace.steps) if hasattr(trace, "steps") else 0,
        total_cost=cost,
        trace=trace_dict,
    )

    # Record per-step transitions for transition_stats (TE-02)
    model_id = os.getenv("NANO_VM_MCP_LLM_MODEL", "__none__") or "__none__"
    steps = trace.steps if hasattr(trace, "steps") else []
    if len(steps) >= 2:
        step_ids = [s.step_id for s in steps]
        for from_s, to_s in zip(step_ids, step_ids[1:]):
            store.upsert_transition(
                program_name=program.name,
                from_step=from_s,
                to_step=to_s,
                model_id=model_id,
            )

    return {
        "trace_id": trace_id,
        "program_id": program_id,
        "status": str(trace.status),
        "steps": len(trace.steps) if hasattr(trace, "steps") else 0,
        "cost": cost,
        "error": None,
    }


async def get_trace(store: ProgramStore, trace_id: str) -> dict[str, Any]:
    """
    Retrieve a full Trace by ID.

    Returns the stored Trace JSON or {"error": "not found"}.
    """
    result = store.get_trace(trace_id)
    if result is None:
        return {"error": f"Trace '{trace_id}' not found"}
    return result


async def list_programs(store: ProgramStore) -> list[dict[str, Any]]:
    """
    List all saved programs (id, name, created_at).
    """
    return store.list_programs()


async def get_program(store: ProgramStore, program_id: str) -> dict[str, Any]:
    """
    Retrieve a saved Program JSON by ID.
    """
    result = store.get_program(program_id)
    if result is None:
        return {"error": f"Program '{program_id}' not found"}
    return result


async def delete_program(store: ProgramStore, program_id: str) -> dict[str, Any]:
    """
    Delete a program and its associated traces.

    Returns {"deleted": true} or {"error": "not found"}.
    """
    ok = store.delete_program(program_id)
    if not ok:
        return {"error": f"Program '{program_id}' not found"}
    return {"deleted": True, "program_id": program_id}


# ---------------------------------------------------------------------------
# vm.step() — sprint_5_mcp_vmstep
# ---------------------------------------------------------------------------
#
# ExecutionVM exposes run() (full execution, dies on first PENDING/SUSPENDED)
# and resume_with_program() (webhook-shaped: requires the caller to re-pass
# the Program and a WebhookEvent). Neither is a clean per-turn primitive for
# a channel adapter (Telegram/Web), which wants: send one input, get one
# output, repeat until terminal. Every existing adapter (tarot-bot's
# pending_executions table) re-invents this gap. vm_step() closes it once,
# in the gateway, without touching nano_vm core:
#   - first call:  program supplied        -> run() until SUCCESS/FAILED/SUSPENDED
#   - later calls: session_id only         -> resume_with_program() from cursor
# The suspend cursor (step_id, state, trace) is bridged from ExecutionVM's
# CursorRepository protocol into ProgramStore.vm_sessions, so it survives
# across MCP calls (each call constructs a fresh ExecutionVM — see
# _build_vm — whose default InMemoryCursorRepository would otherwise die
# with it).


class _GatewayCursorRepository:
    """Bridges nano_vm.vm.CursorRepository protocol to ProgramStore.vm_sessions.

    save()/load()/delete() match the Protocol signature exactly; ExecutionVM
    is given an instance of this instead of the default InMemoryCursorRepository
    so suspend state survives between separate ExecutionVM instances (one per
    MCP call) and process restarts.
    """

    def __init__(self, store: ProgramStore, program_id: str) -> None:
        self._store = store
        self._program_id = program_id

    async def save(
        self, trace_id: str, step_id: str, state: Any, trace: Any
    ) -> None:
        self._store.save_vm_session(
            session_id=trace_id,
            program_id=self._program_id,
            cursor_step_id=step_id,
            state=state.model_dump(mode="json"),
            trace=trace.model_dump(mode="json"),
        )

    async def load(self, trace_id: str) -> tuple[str, Any, Any] | None:
        from nano_vm.models import StateContext, Trace

        row = self._store.get_vm_session(trace_id)
        if row is None:
            return None
        state = StateContext.model_validate(row["state"])
        trace = Trace.model_validate(row["trace"])
        return row["cursor_step_id"], state, trace

    async def delete(self, trace_id: str) -> None:
        self._store.delete_vm_session(trace_id)


def _trace_result(
    session_id: str,
    program_id: str,
    trace: Any,
) -> dict[str, Any]:
    """Project a Trace into the vm_step() response contract.

    status is normalized via str().split(".")[-1] — same pattern as
    _build_debugger_payload — so callers get "SUCCESS"/"SUSPENDED" etc.,
    not the raw "TraceStatus.SUCCESS" enum repr.
    """
    from nano_vm.models import TraceStatus

    raw_status = trace.status
    suspended = raw_status == TraceStatus.SUSPENDED
    status_str = str(raw_status)
    status_str = status_str.split(".")[-1] if "." in status_str else status_str
    return {
        "session_id": session_id,
        "trace_id": str(trace.trace_id),
        "program_id": program_id,
        "status": status_str,
        "suspended": suspended,
        "output": trace.last_output() if not suspended else None,
        "steps": len(trace.steps) if hasattr(trace, "steps") else 0,
        "cost": _extract_cost(trace),
        "error": trace.error if hasattr(trace, "error") else None,
    }


async def vm_step(
    store: ProgramStore,
    session_id: str = "",
    input_data: dict[str, Any] | None = None,
    program: dict[str, Any] | None = None,
    save_as: str = "",
) -> dict[str, Any]:
    """
    One channel-adapter turn: (session_id, input) -> output.

    First call of a session: pass `program` (and optionally `save_as`).
    session_id is empty or unknown -> starts a fresh run, returns a new
    session_id (== trace_id) the caller must pass on subsequent turns.

    Later calls: pass `session_id` only (+ `input_data` for the suspended
    step). Resumes from the persisted cursor via resume_with_program().

    Returns:
        {session_id, trace_id, program_id, status, suspended: bool,
         output, steps, cost, error}
        suspended=True means the FSM is waiting for another vm_step() call
        with the same session_id; suspended=False means the run is terminal
        (SUCCESS/FAILED/BUDGET_EXCEEDED/STALLED).
    """
    input_data = input_data or {}

    # ------------------------------------------------------------------
    # Path A: new session (program supplied)
    # ------------------------------------------------------------------
    if program is not None:
        try:
            parsed_program = Program.model_validate(program)
        except ValidationError as exc:
            return {
                "error": f"Invalid program: {exc.error_count()} validation error(s)",
                "detail": str(exc),
            }

        program_id = str(uuid.uuid4())
        store.save_program(program_id, save_as, program)

        vm_or_err = _build_vm(program)
        if isinstance(vm_or_err, str):
            return {"error": vm_or_err}

        vm_or_err._cursor_repo = _GatewayCursorRepository(store, program_id)

        try:
            trace = await vm_or_err.run(parsed_program, context=input_data)
        except Exception as exc:
            logger.exception("vm_step_run_failed program_id=%s", program_id)
            return {"error": f"Execution failed: {exc}", "program_id": program_id}

        new_session_id = str(trace.trace_id)
        result = _trace_result(new_session_id, program_id, trace)

        # Persist trace snapshot for get_trace() visibility regardless of
        # terminal/suspended state (mirrors run_program's store.save_trace).
        store.save_trace(
            trace_id=new_session_id,
            program_id=program_id,
            status=str(trace.status),
            steps_count=result["steps"],
            total_cost=result["cost"],
            trace=trace.model_dump(mode="json"),
        )
        return result

    # ------------------------------------------------------------------
    # Path B: resume existing session
    # ------------------------------------------------------------------
    if not session_id:
        return {"error": "vm_step requires either 'program' (new session) or 'session_id' (resume)"}

    session_row = store.get_vm_session(session_id)
    if session_row is None:
        return {"error": f"Session '{session_id}' not found or already terminal"}

    program_id = session_row["program_id"]
    program_data = store.get_program(program_id)
    if program_data is None:
        return {"error": f"Program '{program_id}' for session '{session_id}' not found"}

    try:
        parsed_program = Program.model_validate(program_data)
    except ValidationError as exc:
        return {
            "error": (
                f"Stored program '{program_id}' is no longer valid: "
                f"{exc.error_count()} error(s)"
            ),
            "detail": str(exc),
        }

    vm_or_err = _build_vm(program_data)
    if isinstance(vm_or_err, str):
        return {"error": vm_or_err}

    vm_or_err._cursor_repo = _GatewayCursorRepository(store, program_id)

    from nano_vm.vm import ResumeError, WebhookEvent

    event = WebhookEvent(trace_id=session_id, payload=input_data, source="OPERATOR")
    try:
        trace = await vm_or_err.resume_with_program(event, parsed_program)
    except ResumeError as exc:
        return {"error": str(exc), "session_id": session_id}
    except Exception as exc:
        logger.exception("vm_step_resume_failed session_id=%s", session_id)
        return {"error": f"Resume failed: {exc}", "session_id": session_id}

    result = _trace_result(session_id, program_id, trace)
    store.save_trace(
        trace_id=session_id,
        program_id=program_id,
        status=str(trace.status),
        steps_count=result["steps"],
        total_cost=result["cost"],
        trace=trace.model_dump(mode="json"),
    )
    return result
