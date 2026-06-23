"""
Tests for nano_vm_mcp.vmstep — vm.step() gateway primitive
(sprint_5_mcp_vmstep / sprint_channel_adapter_core).
"""

import pytest
from nano_vm import StateContext, Trace

from nano_vm_mcp.store import ProgramStore
from nano_vm_mcp.vmstep import SQLiteCursorRepository, vm_step


@pytest.fixture
def store(tmp_path):
    return ProgramStore(str(tmp_path / "test.db"))


def _suspending_tool(**kwargs):
    return "PENDING"


def _greet_tool(**kwargs):
    webhook = kwargs.get("webhook") or {}
    name = webhook.get("name", "stranger") if isinstance(webhook, dict) else "stranger"
    return f"Hello, {name}!"


def _echo_tool(**kwargs):
    return "noop"


SUSPEND_PROGRAM = {
    "name": "greet_flow",
    "steps": [
        {
            "id": "ask_name",
            "type": "tool",
            "tool": "suspending_tool",
            "args": {},
            "output_key": "name_prompt",
            "next_step": "greet",
        },
        {
            "id": "greet",
            "type": "tool",
            "tool": "greet_tool",
            "args": {"webhook": "$__webhook__"},
            "output_key": "greeting",
            "is_terminal": True,
        },
    ],
}

NO_SUSPEND_PROGRAM = {
    "name": "linear_flow",
    "steps": [
        {
            "id": "only_step",
            "type": "tool",
            "tool": "echo_tool",
            "args": {},
            "output_key": "result",
            "is_terminal": True,
        },
    ],
}

TOOLS = {
    "suspending_tool": _suspending_tool,
    "greet_tool": _greet_tool,
    "echo_tool": _echo_tool,
}


# --- SQLiteCursorRepository (Protocol conformance) --------------------------


async def test_sqlite_cursor_repository_save_and_load(store):
    repo = SQLiteCursorRepository(store)
    state = StateContext(data={"a": 1}, step_outputs={"b": 2})
    trace = Trace(program_name="test")

    await repo.save(trace_id=trace.trace_id, step_id="step_a", state=state, trace=trace)
    result = await repo.load(trace.trace_id)

    assert result is not None
    step_id, loaded_state, loaded_trace = result
    assert step_id == "step_a"
    assert loaded_state.data == {"a": 1}
    assert loaded_trace.trace_id == trace.trace_id


async def test_sqlite_cursor_repository_load_missing(store):
    repo = SQLiteCursorRepository(store)
    assert await repo.load("nonexistent-trace-id") is None


async def test_sqlite_cursor_repository_delete(store):
    repo = SQLiteCursorRepository(store)
    state = StateContext(data={}, step_outputs={})
    trace = Trace(program_name="test")
    await repo.save(trace_id=trace.trace_id, step_id="s", state=state, trace=trace)

    await repo.delete(trace.trace_id)
    assert await repo.load(trace.trace_id) is None


# --- vm_step(): first-call behavior ------------------------------------------


async def test_first_call_requires_program(store):
    result = await vm_step(store, session_id="sess1", input={}, program=None, tools=TOOLS)
    assert "error" in result
    assert "program" in result["error"].lower()


async def test_first_call_invalid_program_returns_error(store):
    result = await vm_step(
        store, session_id="sess1", input={}, program={"not": "a valid program"}, tools=TOOLS
    )
    assert "error" in result


async def test_first_call_no_suspend_returns_success(store):
    result = await vm_step(
        store, session_id="sess1", input={}, program=NO_SUSPEND_PROGRAM, tools=TOOLS
    )
    assert result["error"] is None
    assert result["status"] == "SUCCESS"
    assert result["suspended"] is False


async def test_first_call_suspend_returns_suspended_and_records_session(store):
    result = await vm_step(
        store, session_id="sess1", input={}, program=SUSPEND_PROGRAM, tools=TOOLS
    )
    assert result["error"] is None
    assert result["status"] == "SUSPENDED"
    assert result["suspended"] is True

    session = store.get_vm_session("sess1")
    assert session is not None
    assert session["trace_id"] == result["trace_id"]


async def test_no_suspend_program_does_not_record_session(store):
    await vm_step(store, session_id="sess1", input={}, program=NO_SUSPEND_PROGRAM, tools=TOOLS)
    assert store.get_vm_session("sess1") is None


# --- vm_step(): resume behavior ----------------------------------------------


async def test_resume_completes_suspended_trace(store):
    r1 = await vm_step(store, session_id="sess1", input={}, program=SUSPEND_PROGRAM, tools=TOOLS)
    assert r1["suspended"] is True

    r2 = await vm_step(store, session_id="sess1", input={"name": "Alex"}, tools=TOOLS)
    assert r2["error"] is None
    assert r2["status"] == "SUCCESS"
    assert r2["suspended"] is False
    assert r2["output"] == "Hello, Alex!"


async def test_resume_clears_session_after_completion(store):
    await vm_step(store, session_id="sess1", input={}, program=SUSPEND_PROGRAM, tools=TOOLS)
    await vm_step(store, session_id="sess1", input={"name": "Alex"}, tools=TOOLS)
    assert store.get_vm_session("sess1") is None


async def test_resume_without_program_reuses_stored_program(store):
    """Second call must NOT require `program` again — it is reloaded from store."""
    await vm_step(store, session_id="sess1", input={}, program=SUSPEND_PROGRAM, tools=TOOLS)
    r2 = await vm_step(store, session_id="sess1", input={"name": "Bo"}, program=None, tools=TOOLS)
    assert r2["error"] is None
    assert r2["output"] == "Hello, Bo!"


async def test_two_sessions_are_independent(store):
    """Two different session_ids must not share trace_id/program_id state."""
    r1a = await vm_step(store, session_id="sess_a", input={}, program=SUSPEND_PROGRAM, tools=TOOLS)
    r1b = await vm_step(store, session_id="sess_b", input={}, program=SUSPEND_PROGRAM, tools=TOOLS)
    assert r1a["trace_id"] != r1b["trace_id"]

    r2a = await vm_step(store, session_id="sess_a", input={"name": "A"}, tools=TOOLS)
    r2b = await vm_step(store, session_id="sess_b", input={"name": "B"}, tools=TOOLS)
    assert r2a["output"] == "Hello, A!"
    assert r2b["output"] == "Hello, B!"


async def test_session_survives_fresh_store_instance_same_db(store, tmp_path):
    """
    Regression guard: SQLiteCursorRepository must persist across process
    boundaries (not just across calls within one ProgramStore instance) --
    this is the entire reason it replaced the in-memory design. Simulates a
    process restart by opening a second ProgramStore on the same db file.
    """
    await vm_step(store, session_id="sess1", input={}, program=SUSPEND_PROGRAM, tools=TOOLS)

    db_path = str(tmp_path / "test.db")
    fresh_store = ProgramStore(db_path)

    result = await vm_step(
        fresh_store, session_id="sess1", input={"name": "Restarted"}, tools=TOOLS
    )
    assert result["error"] is None
    assert result["status"] == "SUCCESS"
    assert result["output"] == "Hello, Restarted!"


async def test_resume_with_no_existing_session_and_no_program_errors(store):
    result = await vm_step(store, session_id="ghost_session", input={"x": 1}, tools=TOOLS)
    assert "error" in result
    assert result["error"] is not None
