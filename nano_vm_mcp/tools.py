"""nano_vm_mcp.tools — MCP tool implementations."""
from __future__ import annotations

import uuid
from typing import Any

from nano_vm import ExecutionVM, Program
from pydantic import ValidationError

from .store import ProgramStore


def _vm() -> ExecutionVM:
    """Return a fresh VM instance (stateless per call)."""
    return ExecutionVM()


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

    vm = _vm()
    trace = await vm.run(program)

    trace_id = str(uuid.uuid4())
    trace_dict = trace.model_dump(mode="json") if hasattr(trace, "model_dump") else vars(trace)

    store.save_trace(
        trace_id=trace_id,
        program_id=program_id,
        status=str(trace.status),
        steps_count=len(trace.steps) if hasattr(trace, "steps") else 0,
        total_cost=float(trace.total_cost) if hasattr(trace, "total_cost") else 0.0,
        trace=trace_dict,
    )

    return {
        "trace_id": trace_id,
        "program_id": program_id,
        "status": str(trace.status),
        "steps": len(trace.steps) if hasattr(trace, "steps") else 0,
        "cost": float(trace.total_cost) if hasattr(trace, "total_cost") else 0.0,
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
