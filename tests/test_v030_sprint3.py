"""
tests/test_v030_sprint3.py  [nano_vm_mcp репо]
===============================================
Sprint 3: Assembly & Tombstoning — Gateway

Покрывает:
  - ProgramStore.save_state_context / load_state_context / delete_state_context
  - SQLite WAL: таблица state_contexts создаётся автоматически
  - Изоляция между trace_id
  - Overwrite (INSERT OR REPLACE)
  - GovernedRunProgramHandler: TRACE projection logging после run_program
"""

from __future__ import annotations

import json
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nano_vm_mcp.store import ProgramStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Any) -> ProgramStore:
    db = tmp_path / "test_sprint3.db"
    return ProgramStore(str(db))


# ---------------------------------------------------------------------------
# ProgramStore — state_contexts
# ---------------------------------------------------------------------------


class TestProgramStoreStateContext:
    def test_table_created_automatically(self, store: ProgramStore) -> None:
        # Если таблица не создана — save упадёт; значит просто проверяем что нет ошибки
        store.save_state_context("t1", {"status": "running"})

    def test_save_and_load(self, store: ProgramStore) -> None:
        ctx: dict[str, Any] = {"trace_id": "t1", "status": "running", "steps_count": 3}
        store.save_state_context("t1", ctx)
        loaded = store.load_state_context("t1")

        assert loaded is not None
        assert loaded["trace_id"] == "t1"
        assert loaded["steps_count"] == 3

    def test_load_nonexistent_returns_none(self, store: ProgramStore) -> None:
        assert store.load_state_context("ghost-trace") is None

    def test_save_overwrites(self, store: ProgramStore) -> None:
        store.save_state_context("t1", {"status": "running"})
        store.save_state_context("t1", {"status": "success"})
        loaded = store.load_state_context("t1")

        assert loaded is not None
        assert loaded["status"] == "success"

    def test_delete_existing(self, store: ProgramStore) -> None:
        store.save_state_context("t1", {"foo": "bar"})
        assert store.delete_state_context("t1") is True
        assert store.load_state_context("t1") is None

    def test_delete_nonexistent_returns_false(self, store: ProgramStore) -> None:
        assert store.delete_state_context("ghost") is False

    def test_multiple_trace_ids_isolated(self, store: ProgramStore) -> None:
        store.save_state_context("t1", {"x": 1})
        store.save_state_context("t2", {"x": 2})

        loaded_t1 = store.load_state_context("t1")
        loaded_t2 = store.load_state_context("t2")

        assert loaded_t1 is not None and loaded_t1["x"] == 1
        assert loaded_t2 is not None and loaded_t2["x"] == 2

    def test_delete_one_does_not_affect_other(self, store: ProgramStore) -> None:
        store.save_state_context("t1", {"x": 1})
        store.save_state_context("t2", {"x": 2})
        store.delete_state_context("t1")

        assert store.load_state_context("t1") is None
        assert store.load_state_context("t2") is not None

    def test_roundtrip_complex_payload(self, store: ProgramStore) -> None:
        ctx: dict[str, Any] = {
            "trace_id": "complex-1",
            "status": "SUCCESS",
            "steps_count": 7,
            "projection_target": "TRACE",
            "nested": {"a": [1, 2, 3], "b": None},
        }
        store.save_state_context("complex-1", ctx)
        loaded = store.load_state_context("complex-1")

        assert loaded is not None
        assert loaded["nested"]["a"] == [1, 2, 3]
        assert loaded["nested"]["b"] is None


# ---------------------------------------------------------------------------
# GovernedRunProgramHandler — TRACE projection logging
# ---------------------------------------------------------------------------


class TestGovernedRunProgramHandlerTraceProjection:
    """
    Проверяет что GovernedRunProgramHandler после успешного run_program
    сохраняет TRACE projection в store через save_state_context.
    """

    @pytest.fixture
    def handler(self) -> Any:
        from nano_vm_mcp.handlers import GovernedRunProgramHandler

        return GovernedRunProgramHandler(policy=None)  # policy=None → all allowed

    @pytest.mark.asyncio
    async def test_trace_projection_saved_after_run(
        self, handler: Any, store: ProgramStore
    ) -> None:
        run_result = {
            "trace_id": "trace-abc",
            "status": "SUCCESS",
            "steps_count": 2,
            "total_cost_usd": 0.001,
        }

        with patch("nano_vm_mcp.tools.run_program", new=AsyncMock(return_value=run_result)):
            arguments: dict[str, Any] = {
                "program": {"steps": []},
                "save_as": "test-prog",
            }
            await handler.handle("run_program", arguments, store)

        saved = store.load_state_context("trace-abc")
        assert saved is not None
        assert saved["trace_id"] == "trace-abc"
        assert saved["status"] == "SUCCESS"
        assert saved["projection_target"] == "TRACE"

    @pytest.mark.asyncio
    async def test_trace_projection_not_saved_on_capability_denied(
        self, store: ProgramStore
    ) -> None:
        from nano_vm.models import PolicySnapshot
        from nano_vm_mcp.handlers import GovernedRunProgramHandler

        policy = PolicySnapshot(
            policy_id="strict",
            version="1.0",
            tool_capabilities={"allowed_tool": ["read"]},
        )
        handler = GovernedRunProgramHandler(policy=policy)

        arguments: dict[str, Any] = {
            "program": {
                "steps": [
                    {"id": "s1", "type": "tool", "tool": "forbidden_tool"}
                ]
            }
        }

        result = await handler.handle("run_program", arguments, store)
        response = json.loads(result[0].text)

        assert response["error"] == "capability_denied"
        assert "forbidden_tool" in response["denied_tools"]
        # Projection не сохраняется при deny
        assert store.load_state_context("any") is None

    @pytest.mark.asyncio
    async def test_unknown_tool_not_handled(
        self, handler: Any, store: ProgramStore
    ) -> None:
        result = await handler.handle("nonexistent_tool", {}, store)
        response = json.loads(result[0].text)
        # Передаётся successor → UnknownToolHandler
        assert "error" in response or "unknown" in str(response).lower()

    @pytest.mark.asyncio
    async def test_trace_projection_missing_trace_id_no_crash(
        self, handler: Any, store: ProgramStore
    ) -> None:
        # run_program вернул результат без trace_id — не должно падать
        run_result = {"status": "SUCCESS", "steps_count": 1}

        with patch("nano_vm_mcp.tools.run_program", new=AsyncMock(return_value=run_result)):
            arguments: dict[str, Any] = {"program": {"steps": []}}
            result = await handler.handle("run_program", arguments, store)

        response = json.loads(result[0].text)
        assert response["status"] == "SUCCESS"
