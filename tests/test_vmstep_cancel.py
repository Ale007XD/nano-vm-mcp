"""vm_step() cancellation: session must not be poisoned (cancel-wedge, variant A).

WG-01  cancel mid-resume: CancelledError propagates, session row removed
WG-02  after a cancelled resume, a fresh call with `program` works (no wedge)
WG-03  partial trace of the cancelled resume is stored with status CANCELLED
WG-04  cancel on the first turn: propagates, no session row, trace stored
WG-05  normal resume is unaffected (suspended session still resumable)
"""

import asyncio

import pytest

from nano_vm_mcp.store import ProgramStore
from nano_vm_mcp.vmstep import vm_step


@pytest.fixture
def store(tmp_path):
    return ProgramStore(str(tmp_path / "test.db"))


def _suspending_tool(**kwargs):
    return "PENDING"


async def _slow_tool(**kwargs):
    await asyncio.sleep(30)
    return "never"


def _ok_tool(**kwargs):
    return "ok"


TOOLS = {"suspending_tool": _suspending_tool, "slow_tool": _slow_tool, "ok_tool": _ok_tool}

RESUME_SLOW = {
    "name": "resume_slow",
    "steps": [
        {
            "id": "ask",
            "type": "tool",
            "tool": "suspending_tool",
            "args": {},
            "output_key": "prompt",
            "next_step": "slow",
        },
        {
            "id": "slow",
            "type": "tool",
            "tool": "slow_tool",
            "args": {},
            "output_key": "out",
            "is_terminal": True,
        },
    ],
}

FIRST_SLOW = {
    "name": "first_slow",
    "steps": [
        {
            "id": "slow",
            "type": "tool",
            "tool": "slow_tool",
            "args": {},
            "output_key": "out",
            "is_terminal": True,
        },
    ],
}

RESUME_OK = {
    "name": "resume_ok",
    "steps": [
        {
            "id": "ask",
            "type": "tool",
            "tool": "suspending_tool",
            "args": {},
            "output_key": "prompt",
            "next_step": "done",
        },
        {
            "id": "done",
            "type": "tool",
            "tool": "ok_tool",
            "args": {},
            "output_key": "out",
            "is_terminal": True,
        },
    ],
}


async def _cancel_resume(store, session_id="s1"):
    first = await vm_step(store, session_id, program=RESUME_SLOW, tools=TOOLS)
    assert first["status"] == "SUSPENDED"
    assert store.get_vm_session(session_id) is not None
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(vm_step(store, session_id, input={"x": 1}, tools=TOOLS), 0.3)
    return first["trace_id"]


async def test_wg01_cancel_mid_resume_removes_session(store):
    await _cancel_resume(store)
    assert store.get_vm_session("s1") is None


async def test_wg02_fresh_call_after_cancel_is_not_wedged(store):
    await _cancel_resume(store)
    again = await vm_step(store, "s1", program=RESUME_SLOW, tools=TOOLS)
    assert again.get("error") is None
    assert again["status"] == "SUSPENDED"


async def test_wg03_partial_trace_saved_as_cancelled(store):
    trace_id = await _cancel_resume(store)
    stored = store.get_trace(trace_id)
    assert stored is not None
    assert str(stored.get("status")).lower() == "cancelled"


async def test_wg04_first_turn_cancel_propagates_and_saves_trace(store):
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(vm_step(store, "s2", program=FIRST_SLOW, tools=TOOLS), 0.3)
    assert store.get_vm_session("s2") is None
    rows = store._con.execute("SELECT id FROM traces").fetchall()
    assert len(rows) == 1
    assert store.get_trace(rows[0]["id"])["status"].lower() == "cancelled"


async def test_wg05_normal_resume_unaffected(store):
    first = await vm_step(store, "s3", program=RESUME_OK, tools=TOOLS)
    assert first["suspended"] is True
    done = await vm_step(store, "s3", input={"x": 1}, tools=TOOLS)
    assert done["status"] == "SUCCESS"
    assert store.get_vm_session("s3") is None
