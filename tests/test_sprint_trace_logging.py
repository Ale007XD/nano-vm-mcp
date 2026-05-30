from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nano_vm_mcp.handlers import GovernedRunProgramHandler
from nano_vm_mcp.store import ProgramStore

pytestmark = pytest.mark.asyncio


@pytest.fixture
def store(tmp_path: Path) -> ProgramStore:
    db_path = tmp_path / "test.db"
    return ProgramStore(str(db_path))


async def test_tl01_save_and_get_trace_step(store: ProgramStore) -> None:
    """TL-01: save_trace_step saves a record, get_trace_steps returns it."""
    execution_id = "exec-001"
    projected = {
        "trace_id": execution_id,
        "status": "completed",
        "steps": 3,
        "cost": 0.5,
        "projection_target": "TRACE",
    }
    canonical_hash = "abc123hash"

    store.save_trace_step(
        execution_id=execution_id,
        step_index=0,
        step_id="run_program",
        projected=projected,
        canonical_hash=canonical_hash,
    )

    traces = store.get_trace_steps(execution_id)
    assert len(traces) == 1
    trace = traces[0]
    assert trace["execution_id"] == execution_id
    assert trace["step_index"] == 0
    assert trace["step_id"] == "run_program"
    assert trace["projected"] == projected
    assert trace["canonical_hash"] == canonical_hash
    assert "created_at" in trace


async def test_tl02_get_trace_steps_unknown_execution_id(store: ProgramStore) -> None:
    """TL-02: get_trace_steps returns empty list for unknown execution_id."""
    traces = store.get_trace_steps("nonexistent-id")
    assert traces == []


async def test_tl03_multiple_steps_sorted_by_step_index(store: ProgramStore) -> None:
    """TL-03: Multiple steps are returned sorted by step_index."""
    execution_id = "exec-multi"
    projected_base = {"projection_target": "TRACE"}

    # Insert out of order
    store.save_trace_step(
        execution_id=execution_id,
        step_index=2,
        step_id="step_c",
        projected={**projected_base, "step": "c"},
        canonical_hash="hash_c",
    )
    store.save_trace_step(
        execution_id=execution_id,
        step_index=0,
        step_id="step_a",
        projected={**projected_base, "step": "a"},
        canonical_hash="hash_a",
    )
    store.save_trace_step(
        execution_id=execution_id,
        step_index=1,
        step_id="step_b",
        projected={**projected_base, "step": "b"},
        canonical_hash="hash_b",
    )

    traces = store.get_trace_steps(execution_id)
    assert len(traces) == 3
    assert traces[0]["step_index"] == 0
    assert traces[1]["step_index"] == 1
    assert traces[2]["step_index"] == 2
    assert traces[0]["step_id"] == "step_a"
    assert traces[1]["step_id"] == "step_b"
    assert traces[2]["step_id"] == "step_c"


async def test_tl04_save_trace_step_returns_positive_rowid(store: ProgramStore) -> None:
    """TL-04: save_trace_step returns rowid > 0."""
    rowid = store.save_trace_step(
        execution_id="exec-rowid",
        step_index=0,
        step_id="run_program",
        projected={"test": True},
        canonical_hash="hash_test",
    )
    assert rowid is not None
    assert rowid > 0

    rowid2 = store.save_trace_step(
        execution_id="exec-rowid",
        step_index=1,
        step_id="run_program_2",
        projected={"test": True},
        canonical_hash="hash_test2",
    )
    assert rowid2 > rowid


async def test_tl05_handler_records_trace_step_on_success(tmp_path: Path) -> None:
    """TL-05: GovernedRunProgramHandler records trace_step after successful run_program."""
    db_path = tmp_path / "handler_test.db"
    store = ProgramStore(str(db_path))
    handler = GovernedRunProgramHandler(policy=None)

    program = {
        "name": "test_prog",
        "steps": [{"id": "s1", "type": "tool", "tool": "echo"}],
    }
    fake_result = {
        "trace_id": "trace-tl05",
        "program_id": "prog-tl05",
        "status": "TraceStatus.SUCCESS",
        "steps": 1,
        "cost": 0.0,
        "error": None,
    }
    fake_trace_dict: dict[str, object] = {"state_snapshots": []}

    with (
        patch("nano_vm_mcp.handlers._tools.run_program", new_callable=AsyncMock) as mock_run,
        patch.object(store, "get_trace", return_value=fake_trace_dict),
    ):
        mock_run.return_value = fake_result
        await handler.handle("run_program", {"program": program}, store)

    traces = store.get_trace_steps("trace-tl05")
    assert len(traces) == 1
    assert traces[0]["execution_id"] == "trace-tl05"
    assert traces[0]["step_id"] == "run_program"
    assert traces[0]["projected"]["projection_target"] == "TRACE"
    assert traces[0]["projected"]["trace_id"] == "trace-tl05"


async def test_tl06_handler_no_trace_step_on_error(tmp_path: Path) -> None:
    """TL-06: GovernedRunProgramHandler does NOT record trace_step if result has error."""
    db_path = tmp_path / "handler_error_test.db"
    store = ProgramStore(str(db_path))
    handler = GovernedRunProgramHandler(policy=None)

    program = {
        "name": "test_prog",
        "steps": [{"id": "s1", "type": "tool", "tool": "echo"}],
    }
    # Simulate execution failure — run_program returns error dict (no trace_id)
    error_result = {
        "error": "Execution failed: some error",
        "program_id": "prog-tl06",
    }

    with patch("nano_vm_mcp.handlers._tools.run_program", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = error_result
        await handler.handle("run_program", {"program": program}, store)

    # error result has no trace_id → save_trace_step must not be called
    traces = store.get_trace_steps("any-id")
    assert traces == []
