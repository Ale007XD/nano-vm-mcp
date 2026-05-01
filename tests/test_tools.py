"""Tests for MCP tool handlers (mock VM)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nano_vm_mcp.store import ProgramStore
from nano_vm_mcp import tools


@pytest.fixture
def store(tmp_path):
    return ProgramStore(str(tmp_path / "test.db"))


# ---------------------------------------------------------------------------
# run_program
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_program_invalid_schema(store):
    result = await tools.run_program(store, {"not_a_program": True})
    assert "error" in result
    assert "Invalid program" in result["error"]


@pytest.mark.asyncio
async def test_run_program_saves_trace(store):
    """run_program with a valid program saves a trace and returns trace_id."""
    fake_trace = MagicMock()
    fake_trace.status = "COMPLETED"
    fake_trace.steps = []
    fake_trace.total_cost_usd = MagicMock(return_value=0.0)
    fake_trace.model_dump = MagicMock(return_value={"status": "COMPLETED", "steps": []})

    minimal_program = {"steps": [{"id": "s1", "type": "tool", "tool": "noop"}]}

    with patch("nano_vm_mcp.tools.ExecutionVM") as MockVM:
        instance = MockVM.return_value
        instance.run = AsyncMock(return_value=fake_trace)

        result = await tools.run_program(store, minimal_program, save_as="test-prog")

    assert "trace_id" in result
    assert result["error"] is None
    # Trace persisted
    trace = store.get_trace(result["trace_id"])
    assert trace is not None


# ---------------------------------------------------------------------------
# get_trace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_trace_found(store):
    store.save_program("p1", "prog", {"steps": []})
    store.save_trace("t1", "p1", "COMPLETED", 0, 0.0, {"status": "COMPLETED"})
    result = await tools.get_trace(store, "t1")
    assert result["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_get_trace_not_found(store):
    result = await tools.get_trace(store, "missing")
    assert "error" in result


# ---------------------------------------------------------------------------
# list_programs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_programs_empty(store):
    result = await tools.list_programs(store)
    assert result == []


@pytest.mark.asyncio
async def test_list_programs_returns_items(store):
    store.save_program("p1", "first", {"steps": []})
    store.save_program("p2", "second", {"steps": []})
    result = await tools.list_programs(store)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# get_program
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_program_found(store):
    store.save_program("p1", "prog", {"steps": [{"id": "s1"}]})
    result = await tools.get_program(store, "p1")
    assert result == {"steps": [{"id": "s1"}]}


@pytest.mark.asyncio
async def test_get_program_not_found(store):
    result = await tools.get_program(store, "nope")
    assert "error" in result


# ---------------------------------------------------------------------------
# delete_program
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_program_ok(store):
    store.save_program("p1", "prog", {"steps": []})
    result = await tools.delete_program(store, "p1")
    assert result["deleted"] is True


@pytest.mark.asyncio
async def test_delete_program_not_found(store):
    result = await tools.delete_program(store, "ghost")
    assert "error" in result
