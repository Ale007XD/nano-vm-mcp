"""
Regression tests for DECISIONS.md 2026-06-29 nano-vm-mcp-v0.4.6-snapshot-audit,
defect_2: vmstep.py::vm_step never called store.save_trace() on either branch
(first-run or resume), leaving get_trace()/Agent Debugger/TraceAnalyzer blind
to every execution routed through vm_step().

Fix under test: a new _save_trace() helper in vmstep.py, called unconditionally
(terminal and suspended both) on the first-run branch and the resume branch.

Naming follows the project's VS-xx test-id convention used in
test_sprint5_mcp_vmstep.py (CONSTRAINTS.md / SPRINTS.md sprint_5_mcp_vmstep).
These are new IDs (VS-19..VS-23), not a renumbering of the existing VS-01..18
suite, since this file targets vmstep.py specifically, not tools.py's dead
_GatewayCursorRepository path (defect_1, separate fix, separate test file).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from nano_vm import Program
from nano_vm.models import OnError, Step, StepType

from nano_vm_mcp.store import ProgramStore
from nano_vm_mcp.vmstep import vm_step

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> ProgramStore:
    """Fresh in-memory SQLite store per test — no cross-test state."""
    return ProgramStore(":memory:")


def _terminal_tool_program(name: str = "terminal_prog") -> dict[str, Any]:
    """
    Single-step TOOL program, is_terminal=True, never suspends.
    Mirrors the repro shape in DECISIONS.md defect_2
    ("single-step terminal TOOL program").
    """
    program = Program(
        name=name,
        steps=[
            Step(
                id="only_step",
                type=StepType.TOOL,
                tool="echo_tool",
                output_key="result",
                is_terminal=True,
                on_error=OnError.FAIL,
            )
        ],
    )
    return program.model_dump(mode="json")


def _suspend_then_resume_program(name: str = "suspend_prog") -> dict[str, Any]:
    """
    Single-step TOOL program whose tool function returns the sentinel
    string "PENDING" on the first call. vm.py treats a TOOL step's output
    of exactly "PENDING" as a suspend signal (StepStatus.PENDING ->
    Trace.status = SUSPENDED) -- see nano_vm/vm.py around the TOOL-step
    output check. is_terminal=True so that once the tool stops returning
    "PENDING" (on resume), the FSM completes rather than advancing to a
    next step that doesn't exist.
    """
    program = Program(
        name=name,
        steps=[
            Step(
                id="gate_step",
                type=StepType.TOOL,
                tool="suspend_once_tool",
                output_key="result",
                is_terminal=True,
                on_error=OnError.FAIL,
            )
        ],
    )
    return program.model_dump(mode="json")


def _echo_tool(**kwargs: Any) -> str:
    return "DONE"


def _make_suspend_once_tool() -> Any:
    """Returns 'PENDING' exactly once, then 'DONE' on every subsequent call."""
    state = {"called": False}

    def _tool(**kwargs: Any) -> str:
        if not state["called"]:
            state["called"] = True
            return "PENDING"
        return "DONE"

    return _tool


# ---------------------------------------------------------------------------
# VS-19: first-run, terminal (no suspend) -> save_trace() called, get_trace() non-None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vs19_first_run_terminal_persists_trace(store: ProgramStore) -> None:
    session_id = f"session-{uuid.uuid4()}"
    program = _terminal_tool_program()

    result = await vm_step(
        store=store,
        session_id=session_id,
        input={},
        program=program,
        tools={"echo_tool": _echo_tool},
    )

    assert result["error"] is None
    assert result["suspended"] is False
    assert result["status"] == "SUCCESS"

    trace_id = result["trace_id"]
    persisted = store.get_trace(trace_id)
    assert persisted is not None, (
        "defect_2 regression: store.get_trace() returned None for a "
        "terminal vm_step() execution -- save_trace() was not called."
    )
    assert persisted["trace_id"] == trace_id


# ---------------------------------------------------------------------------
# VS-20: first-run, suspends -> save_trace() called even while SUSPENDED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vs20_first_run_suspended_persists_trace(store: ProgramStore) -> None:
    session_id = f"session-{uuid.uuid4()}"
    program = _suspend_then_resume_program()
    suspend_tool = _make_suspend_once_tool()

    result = await vm_step(
        store=store,
        session_id=session_id,
        input={},
        program=program,
        tools={"suspend_once_tool": suspend_tool},
    )

    assert result["error"] is None
    assert result["suspended"] is True
    assert result["status"] == "SUSPENDED"

    trace_id = result["trace_id"]
    persisted = store.get_trace(trace_id)
    assert persisted is not None, (
        "defect_2 regression: store.get_trace() returned None for a "
        "SUSPENDED vm_step() execution -- save_trace() was not called "
        "on the suspend path."
    )
    # persisted is store.get_trace()'s return value -- the parsed trace_json
    # blob (trace.model_dump(mode="json")), where Pydantic's JSON mode
    # serializes the TraceStatus enum via its .value ("suspended"), not via
    # str() ("TraceStatus.SUSPENDED") -- that str() form lives in the SQL
    # status column instead (set via _save_trace's status=str(trace.status)
    # argument), a separate field from trace_json's embedded copy.
    assert persisted["status"] == "suspended"

    raw_row = store._con.execute("SELECT status FROM traces WHERE id = ?", (trace_id,)).fetchone()
    assert raw_row["status"] == "TraceStatus.SUSPENDED", (
        "SQL status column should hold str(trace.status) verbatim, matching "
        "tools.py::run_program's save_trace() call shape exactly."
    )


# ---------------------------------------------------------------------------
# VS-21: resume -> save_trace() called again, trace visible post-resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vs21_resume_persists_trace(store: ProgramStore) -> None:
    session_id = f"session-{uuid.uuid4()}"
    program = _suspend_then_resume_program()
    suspend_tool = _make_suspend_once_tool()

    first = await vm_step(
        store=store,
        session_id=session_id,
        input={},
        program=program,
        tools={"suspend_once_tool": suspend_tool},
    )
    assert first["suspended"] is True

    second = await vm_step(
        store=store,
        session_id=session_id,
        input={"resume_payload": "go"},
        tools={"suspend_once_tool": suspend_tool},
    )

    assert second["error"] is None
    assert second["suspended"] is False
    assert second["status"] == "SUCCESS"

    trace_id = second["trace_id"]
    persisted = store.get_trace(trace_id)
    assert persisted is not None, (
        "defect_2 regression: store.get_trace() returned None after "
        "resume_with_program() completed -- save_trace() was not called "
        "on the resume path."
    )


# ---------------------------------------------------------------------------
# VS-22: vm_session is still correctly cleaned up post-resume (no regression
# on the part of the fix that was already working -- cursor bridge itself)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vs22_resume_cleans_up_vm_session_no_regression(store: ProgramStore) -> None:
    session_id = f"session-{uuid.uuid4()}"
    program = _suspend_then_resume_program()
    suspend_tool = _make_suspend_once_tool()

    await vm_step(
        store=store,
        session_id=session_id,
        input={},
        program=program,
        tools={"suspend_once_tool": suspend_tool},
    )
    assert store.get_vm_session(session_id) is not None

    await vm_step(
        store=store,
        session_id=session_id,
        input={"resume_payload": "go"},
        tools={"suspend_once_tool": suspend_tool},
    )

    assert store.get_vm_session(session_id) is None, (
        "vm_session cleanup on terminal resume regressed -- unrelated to "
        "defect_2 but must not break as a side effect of the _save_trace() patch."
    )


# ---------------------------------------------------------------------------
# VS-23: persisted trace_json round-trips and carries steps_count > 0
# (catches a _save_trace() that calls store.save_trace() with the wrong
# trace serialization, e.g. passing the Trace object instead of
# trace.model_dump(mode="json"))
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vs23_persisted_trace_has_steps_count(store: ProgramStore) -> None:
    session_id = f"session-{uuid.uuid4()}"
    program = _terminal_tool_program()

    result = await vm_step(
        store=store,
        session_id=session_id,
        input={},
        program=program,
        tools={"echo_tool": _echo_tool},
    )

    trace_id = result["trace_id"]
    raw_row = store._con.execute(
        "SELECT steps_count, trace_json FROM traces WHERE id = ?", (trace_id,)
    ).fetchone()
    assert raw_row is not None
    assert raw_row["steps_count"] == 1, (
        "steps_count persisted by _save_trace() does not match the single "
        "TOOL step executed -- check len(trace.steps) source field."
    )
    assert "only_step" in raw_row["trace_json"]
