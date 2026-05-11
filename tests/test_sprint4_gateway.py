"""
tests/test_sprint4_gateway.py
==============================
Sprint4 Gateway: GovernanceEnvelope модель, store методы, GovernedRunProgramHandler.
Репо: nano_vm_mcp
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid

import pytest

from nano_vm_mcp.handlers import GovernanceEnvelope, GovernedRunProgramHandler
from nano_vm_mcp.store import ProgramStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store() -> ProgramStore:
    return ProgramStore(tempfile.mktemp(suffix=".db"))


def _envelope(**kwargs) -> GovernanceEnvelope:
    defaults = dict(
        execution_id="exec-1",
        step_id=0,
        policy_hash="ph",
        canonical_snapshot_hash="sh",
        payload={"status": "SUCCESS"},
    )
    return GovernanceEnvelope(**(defaults | kwargs))


# ---------------------------------------------------------------------------
# GovernanceEnvelope — модель
# ---------------------------------------------------------------------------


class TestGovernanceEnvelope:
    def test_frozen(self) -> None:
        env = _envelope()
        with pytest.raises(Exception):
            env.step_id = 99  # type: ignore[misc]

    def test_fields(self) -> None:
        env = _envelope(
            execution_id="exec-2",
            step_id=3,
            policy_hash="ph2",
            canonical_snapshot_hash="sh2",
            payload=[1, 2, 3],
        )
        assert env.execution_id == "exec-2"
        assert env.step_id == 3
        assert env.policy_hash == "ph2"
        assert env.canonical_snapshot_hash == "sh2"
        assert env.payload == [1, 2, 3]

    def test_payload_dict(self) -> None:
        assert _envelope(payload={"k": "v"}).payload == {"k": "v"}

    def test_payload_list(self) -> None:
        assert _envelope(payload=[1, "two", None]).payload == [1, "two", None]


# ---------------------------------------------------------------------------
# ProgramStore — governance_envelopes
# ---------------------------------------------------------------------------


class TestGovernanceEnvelopeStore:
    def test_save_and_get(self) -> None:
        store = _store()
        eid = str(uuid.uuid4())

        rowid = store.save_envelope(
            execution_id=eid,
            step_id=0,
            policy_hash="ph",
            snapshot_hash="sh",
            payload={"status": "ok"},
        )
        assert isinstance(rowid, int)

        rows = store.get_envelopes(eid)
        assert len(rows) == 1
        assert rows[0]["execution_id"] == eid
        assert rows[0]["step_id"] == 0
        assert rows[0]["policy_hash"] == "ph"
        assert rows[0]["canonical_snapshot_hash"] == "sh"
        assert rows[0]["payload"] == {"status": "ok"}
        assert "created_at" in rows[0]

    def test_multiple_steps_ordered_by_step_id(self) -> None:
        store = _store()
        eid = str(uuid.uuid4())
        for i in reversed(range(5)):
            store.save_envelope(
                eid, step_id=i, policy_hash="ph", snapshot_hash=f"sh{i}", payload={"i": i}
            )
        assert [r["step_id"] for r in store.get_envelopes(eid)] == list(range(5))

    def test_get_envelopes_empty(self) -> None:
        assert _store().get_envelopes("nonexistent") == []

    def test_delete_envelopes(self) -> None:
        store = _store()
        eid = str(uuid.uuid4())
        store.save_envelope(eid, 0, "ph", "sh", {"x": 1})
        store.save_envelope(eid, 1, "ph", "sh2", {"x": 2})
        assert store.delete_envelopes(eid) == 2
        assert store.get_envelopes(eid) == []

    def test_delete_nonexistent_returns_zero(self) -> None:
        assert _store().delete_envelopes("nonexistent") == 0

    def test_list_payload_roundtrip(self) -> None:
        store = _store()
        eid = str(uuid.uuid4())
        store.save_envelope(eid, 0, "ph", "sh", [1, "two", None])
        assert store.get_envelopes(eid)[0]["payload"] == [1, "two", None]

    def test_isolation_between_execution_ids(self) -> None:
        store = _store()
        eid1, eid2 = str(uuid.uuid4()), str(uuid.uuid4())
        store.save_envelope(eid1, 0, "ph", "sh", {"owner": "eid1"})
        store.save_envelope(eid2, 0, "ph", "sh", {"owner": "eid2"})
        assert store.get_envelopes(eid1)[0]["payload"]["owner"] == "eid1"
        assert store.get_envelopes(eid2)[0]["payload"]["owner"] == "eid2"


# ---------------------------------------------------------------------------
# GovernedRunProgramHandler — capability gate + envelope persistence
# ---------------------------------------------------------------------------


def _fake_run_factory(trace_id: str, steps: int = 2):
    async def _fake(store, program_data, save_as=""):
        return {
            "trace_id": trace_id,
            "program_id": "prog-1",
            "status": "success",
            "steps": steps,
            "cost": 0.0,
            "error": None,
        }

    return _fake


class TestGovernedRunProgramHandlerEnvelope:
    @pytest.mark.asyncio
    async def test_envelope_saved_on_success(self) -> None:
        """Успешный run → envelope сохранён в store."""
        store = _store()
        handler = GovernedRunProgramHandler(policy=None)
        trace_id = str(uuid.uuid4())

        # FK: programs(id) должна существовать до вставки в traces
        store.save_program("prog-1", "test-prog", {"steps": []})

        # Сохраняем trace в store с state_snapshots (имитируем run_program)
        fake_trace = {
            "state_snapshots": [[0, "abc123"], [1, "def456"]],
            "status": "success",
            "steps": [],
        }
        store.save_trace(
            trace_id=trace_id,
            program_id="prog-1",
            status="success",
            steps_count=2,
            total_cost=0.0,
            trace=fake_trace,
        )

        import nano_vm_mcp.tools as _tools_module

        original = _tools_module.run_program
        _tools_module.run_program = _fake_run_factory(trace_id, steps=2)
        try:
            program = {"steps": [{"id": "s1", "type": "tool", "tool": "noop"}]}
            result_content = await handler.handle("run_program", {"program": program}, store)
        finally:
            _tools_module.run_program = original

        result = json.loads(result_content[0].text)
        assert result.get("error") is None

        envelopes = store.get_envelopes(trace_id)
        assert len(envelopes) == 1
        env = envelopes[0]
        assert env["execution_id"] == trace_id
        assert env["step_id"] == 1  # max(2-1, 0)
        assert env["policy_hash"] == ""  # policy=None
        assert env["payload"]["projection_target"] == "TRACE"
        assert len(env["canonical_snapshot_hash"]) == 64  # sha256 hex

    @pytest.mark.asyncio
    async def test_error_result_no_envelope(self) -> None:
        """run_program вернул error → envelope не создаётся."""
        store = _store()
        handler = GovernedRunProgramHandler(policy=None)

        import nano_vm_mcp.tools as _tools_module

        original = _tools_module.run_program

        async def _fake_error(s, program_data, save_as=""):
            return {"error": "Execution failed: Tool 'noop' not registered"}

        _tools_module.run_program = _fake_error
        try:
            program = {"steps": [{"id": "s1", "type": "tool", "tool": "noop"}]}
            result_content = await handler.handle("run_program", {"program": program}, store)
        finally:
            _tools_module.run_program = original

        result = json.loads(result_content[0].text)
        assert result.get("error") is not None
        assert store.get_envelopes("any") == []

    @pytest.mark.asyncio
    async def test_capability_denied_no_envelope(self) -> None:
        """Capability denied → envelope не создаётся."""
        from nano_vm.models import PolicySnapshot

        policy = PolicySnapshot(policy_id="p1", version="1.0", tool_capabilities={})
        store = _store()
        handler = GovernedRunProgramHandler(policy=policy)

        program = {"steps": [{"id": "s1", "type": "tool", "tool": "send_email"}]}
        result_content = await handler.handle("run_program", {"program": program}, store)
        result = json.loads(result_content[0].text)

        assert result["error"] == "capability_denied"
        assert "send_email" in result["denied_tools"]
        assert store.get_envelopes("any") == []

    @pytest.mark.asyncio
    async def test_canonical_snapshot_hash_empty_when_trace_missing(self) -> None:
        """Trace не найден в store → canonical_snapshot_hash = sha256('empty')."""
        store = _store()
        handler = GovernedRunProgramHandler(policy=None)
        trace_id = str(uuid.uuid4())

        import nano_vm_mcp.tools as _tools_module

        original = _tools_module.run_program
        _tools_module.run_program = _fake_run_factory(trace_id, steps=1)
        try:
            program = {"steps": [{"id": "s1", "type": "tool", "tool": "noop"}]}
            await handler.handle("run_program", {"program": program}, store)
        finally:
            _tools_module.run_program = original

        envelopes = store.get_envelopes(trace_id)
        assert len(envelopes) == 1
        expected = hashlib.sha256(b"empty").hexdigest()
        assert envelopes[0]["canonical_snapshot_hash"] == expected
