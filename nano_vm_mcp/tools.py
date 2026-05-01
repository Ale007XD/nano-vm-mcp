"""nano_vm_mcp.tools — MCP tool implementations."""

from __future__ import annotations

import os
import uuid
from typing import Any

from nano_vm import ExecutionVM, Program
from nano_vm.adapters import MockLLMAdapter
from pydantic import ValidationError

from .store import ProgramStore


def _has_llm_steps(program_data: dict[str, Any]) -> bool:
    """Return True if any step (including parallel sub-steps) requires an LLM."""

    def _scan(steps: list[dict]) -> bool:
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
            "LiteLLMAdapter is not available. Install it with: pip install 'llm-nano-vm[litellm]'"
        )
    return ExecutionVM(llm=LiteLLMAdapter(model))


async def run_program(
    store: ProgramStore,
    program_data: dict[str, Any],
    save_as: str = "",
) -> dict[str, Any]:
    """
    Validate and execute a Program dict.

    Args:
        store: ProgramStore instance.
        program_data: Raw dict conforming to nano_vm.Program schema.
        save_as: Optional name to persist the program in the store.

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
    trace = await vm_or_err.run(program)

    trace_id = str(uuid.uuid4())
    trace_dict = trace.model_dump(mode="json") if hasattr(trace, "model_dump") else vars(trace)
    # Compute cost in a backward-compatible way
    if hasattr(trace, "total_cost_usd"):
        cost = trace.total_cost_usd() or 0.0
    elif hasattr(trace, "total_cost"):
        cost = float(trace.total_cost) or 0.0
    else:
        cost = 0.0

    store.save_trace(
        trace_id=trace_id,
        program_id=program_id,
        status=str(trace.status),
        steps_count=len(trace.steps) if hasattr(trace, "steps") else 0,
        total_cost=cost,
        trace=trace_dict,
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
