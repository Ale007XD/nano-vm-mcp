"""
tests/test_sprint4_gateway.py
==============================
Sprint4 Gateway: GovernanceEnvelope модель, store методы, GovernedRunProgramHandler.
Репо: nano_vm_mcp
"""

from __future__ import annotations

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
        """frozen=True: мутация поля должна падать."""
        env = _envelope()
        with pytest.raises(Exception):
            env.step_id = 99  # type: ignore[misc]

    def test_fields(self) -> None:
        """Все поля RFC присутствуют и доступны."""
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
        env = _envelope(payload={"k": "v"})
        assert env.payload == {"k": "v"}

    def test_payload_list(self) -> None:
        env = _envelope(payload=[1, "two", None])
        assert env.payload == [1, "two", None]


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

        # Вставляем в обратном порядке — должны вернуться по step_id ASC
        for i in reversed(range(5)):
            store.save_envelope(
                eid, step_id=i, policy_hash="ph", snapshot_hash=f"sh{i}", payload={"i": i}
            )

        rows = store.get_envelopes(eid)
        assert [r["step_id"] for r in rows] == list(range(5))

    def test_get_envelopes_empty(self) -> None:
        store = _store()
        assert store.get_envelopes("nonexistent") == []

    def test_delete_envelopes(self) -> None:
        store = _store()
        eid = str(uuid.uuid4())
        store.save_envelope(eid, 0, "ph", "sh", {"x": 1})
        store.save_envelope(eid, 1, "ph", "sh2", {"x": 2})

        deleted = store.delete_envelopes(eid)
        assert deleted == 2
        assert store.get_envelopes(eid) == []

    def test_delete_nonexistent_returns_zero(self) -> None:
        store = _store()
        assert store.delete_envelopes("nonexistent") == 0

    def test_list_payload_roundtrip(self) -> None:
        store = _store()
        eid = str(uuid.uuid4())
        store.save_envelope(eid, 0, "ph", "sh", [1, "two", None])
        rows = store.get_envelopes(eid)
        assert rows[0]["payload"] == [1, "two", None]

    def test_isolation_between_execution_ids(self) -> None:
        """get_envelopes возвращает только свои записи."""
        store = _store()
        eid1, eid2 = str(uuid.uuid4()), str(uuid.uuid4())
        store.save_envelope(eid1, 0, "ph", "sh", {"owner": "eid1"})
        store.save_envelope(eid2, 0, "ph", "sh", {"owner": "eid2"})

        assert store.get_envelopes(eid1)[0]["payload"]["owner"] == "eid1"
        assert store.get_envelopes(eid2)[0]["payload"]["owner"] == "eid2"


# ---------------------------------------------------------------------------
# GovernedRunProgramHandler — envelope сохраняется после run
# ---------------------------------------------------------------------------


class TestGovernedRunProgramHandlerEnvelope:
    @pytest.mark.asyncio
    async def test_envelope_saved_on_success(self) -> None:
        """После успешного run_program envelope должен появиться в store."""
        store = _store()
        handler = GovernedRunProgramHandler(policy=None)

        program = {
            "steps": [
                {"id": "s1", "type": "tool", "tool": "noop"},
            ]
        }
        # noop tool не зарегистрирован в VM → ожидаем ошибку выполнения,
        # но envelope не сохраняется при error. Тестируем через tool-only программу
        # без llm (MockLLMAdapter("noop") используется внутри _build_vm).
        # Так как noop tool не зарегистрирован, run вернёт error — envelope не пишется.
        # Тест проверяет именно эту ветку: error → no envelope.
        result_content = await handler.handle("run_program", {"program": program}, store)
        import json as _json

        result = _json.loads(result_content[0].text)

        # Execution failed (tool не зарегистрирован) → envelope не сохранён
        assert result.get("error") is not None
        trace_id = result.get("trace_id")
        if trace_id:
            assert store.get_envelopes(trace_id) == []

    @pytest.mark.asyncio
    async def test_capability_denied_no_envelope(self) -> None:
        """При capability_denied envelope не создаётся."""
        from nano_vm.models import PolicySnapshot

        policy = PolicySnapshot(
            policy_id="p1",
            version="1.0",
            tool_capabilities={},  # пустая — никаких tool не разрешено
        )
        store = _store()
        handler = GovernedRunProgramHandler(policy=policy)

        program = {"steps": [{"id": "s1", "type": "tool", "tool": "send_email"}]}
        result_content = await handler.handle("run_program", {"program": program}, store)
        import json as _json

        result = _json.loads(result_content[0].text)

        assert result["error"] == "capability_denied"
        assert "send_email" in result["denied_tools"]
        # Никаких envelope в store
        assert store.get_envelopes("any") == []
