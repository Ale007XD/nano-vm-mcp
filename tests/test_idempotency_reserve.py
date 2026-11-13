"""G-30: atomic idempotency reservation (nano-vm-mcp). IR-01..IR-09."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import Any

import pytest

import nano_vm_mcp.tools as _tools_module
from nano_vm_mcp.handlers import GovernedRunProgramHandler
from nano_vm_mcp.store import ProgramStore

_PROGRAM = {"steps": [{"id": "s1", "type": "tool", "tool": "noop"}]}


@pytest.fixture
def store(tmp_path):
    return ProgramStore(str(tmp_path / "t.db"))


def test_ir01_reserve_is_exclusive(store):
    assert store.reserve_idempotency_key("A", lease_seconds=60) is True
    assert store.reserve_idempotency_key("A", lease_seconds=60) is False
    assert store.get_idempotency_key("A")["status"] == "pending"


def test_ir02_failed_key_is_reservable_again(store):
    assert store.reserve_idempotency_key("A")
    assert store.fail_idempotency_key("A") is True
    assert store.get_idempotency_key("A")["status"] == "failed"
    assert store.reserve_idempotency_key("A") is True
    assert store.get_idempotency_key("A")["status"] == "pending"


def test_ir03_expired_pending_is_reclaimable_and_hidden_from_get(store):
    store.save_idempotency_key("A", "", "pending", None, "2000-01-01T00:00:00Z")
    assert store.get_idempotency_key("A") is None
    assert store.reserve_idempotency_key("A", lease_seconds=60) is True


def test_ir04_fail_never_touches_success(store):
    store.save_idempotency_key("A", "t1", "success", {"x": 1}, None)
    assert store.fail_idempotency_key("A") is False
    assert store.reserve_idempotency_key("A") is False
    assert store.get_idempotency_key("A")["status"] == "success"


def test_ir05_cross_connection_single_winner(tmp_path):
    db = str(tmp_path / "w.db")
    ProgramStore(db)
    wins: list[bool] = []
    barrier = threading.Barrier(8)

    def worker():
        s = ProgramStore(db)
        barrier.wait(timeout=5)
        wins.append(s.reserve_idempotency_key("K", lease_seconds=60))

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join(timeout=15) for t in ts]
    assert sorted(wins).count(True) == 1 and len(wins) == 8


# ---- handler level (same fake as tools/s0_repro IK tests) ----


def _seed(store: ProgramStore, trace_id: str) -> None:
    store.save_program("prog-1", "test-prog", {"steps": []})
    store.save_trace(
        trace_id=trace_id,
        program_id="prog-1",
        status="success",
        steps_count=1,
        total_cost=0.0,
        trace={"state_snapshots": [[0, "abc"]], "status": "success", "steps": []},
    )


def _fake(calls: list[int], delay: float, trace_id: str, mode: str = "ok"):
    async def _run(store, program_data, save_as=""):
        calls.append(1)
        await asyncio.sleep(delay)
        if mode == "raise":
            raise RuntimeError("boom")
        if mode == "error":
            return {"trace_id": None, "status": "failed", "steps": 0, "cost": 0.0, "error": "e"}
        return {
            "trace_id": trace_id,
            "program_id": "prog-1",
            "status": "success",
            "steps": 1,
            "cost": 0.0,
            "error": None,
        }

    return _run


async def _call(h, store, key) -> dict[str, Any]:
    out = await h.handle("run_program", {"program": _PROGRAM, "idempotency_key": key}, store)
    return json.loads(out[0].text)


@pytest.fixture
def env(store):
    tid = str(uuid.uuid4())
    _seed(store, tid)
    orig = _tools_module.run_program
    yield store, tid
    _tools_module.run_program = orig


def test_ir06_concurrent_second_gets_in_flight_then_cache(env):
    store, tid = env
    calls: list[int] = []
    _tools_module.run_program = _fake(calls, 0.05, tid)
    h = GovernedRunProgramHandler(policy=None)

    async def scenario():
        a, b = await asyncio.gather(_call(h, store, "K"), _call(h, store, "K"))
        c = await _call(h, store, "K")
        return a, b, c

    a, b, c = asyncio.run(scenario())
    assert len(calls) == 1
    assert {a.get("error"), b.get("error")} == {None, "idempotency_in_flight"}
    assert c.get("trace_id") == tid and c.get("error") is None


def test_ir07_exception_releases_key_and_retry_runs(env):
    store, tid = env
    calls: list[int] = []
    _tools_module.run_program = _fake(calls, 0.0, tid, mode="raise")
    h = GovernedRunProgramHandler(policy=None)
    with pytest.raises(RuntimeError):
        asyncio.run(_call(h, store, "K"))
    assert store.get_idempotency_key("K")["status"] == "failed"
    _tools_module.run_program = _fake(calls, 0.0, tid)
    assert asyncio.run(_call(h, store, "K")).get("trace_id") == tid
    assert len(calls) == 2


def test_ir08_error_result_marks_failed_and_retry_runs(env):
    store, tid = env
    calls: list[int] = []
    _tools_module.run_program = _fake(calls, 0.0, tid, mode="error")
    h = GovernedRunProgramHandler(policy=None)
    asyncio.run(_call(h, store, "K"))
    assert store.get_idempotency_key("K")["status"] == "failed"
    _tools_module.run_program = _fake(calls, 0.0, tid)
    asyncio.run(_call(h, store, "K"))
    assert len(calls) == 2 and store.get_idempotency_key("K")["status"] == "success"


def test_ir09_cancellation_releases_key(env):
    store, tid = env
    _tools_module.run_program = _fake([], 5.0, tid)
    h = GovernedRunProgramHandler(policy=None)

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_call(h, store, "K"), timeout=0.05)

    asyncio.run(scenario())
    assert store.get_idempotency_key("K")["status"] == "failed"
