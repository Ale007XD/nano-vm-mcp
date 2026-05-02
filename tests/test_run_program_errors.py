"""tests/test_run_program_errors.py — run_program exception handling tests.

Дополняет test_tools.py. Покрывает сценарии когда vm.run() бросает исключение:
- возвращает {"error": ...} вместо проброса исключения наверх
- логирует vm_run_failed с program_id
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nano_vm_mcp.store import ProgramStore
from nano_vm_mcp.tools import run_program

MINIMAL_PROGRAM = {"steps": [{"id": "s1", "type": "tool", "tool": "noop"}]}


@pytest.fixture
def store(tmp_path):
    return ProgramStore(str(tmp_path / "test.db"))


# ---------------------------------------------------------------------------
# vm.run() raises — структурированный ответ, не исключение
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_program_vm_exception_returns_error(store):
    """vm.run() исключение не должно всплывать — возвращается {"error": ...}."""
    with patch("nano_vm_mcp.tools.ExecutionVM") as MockVM:
        instance = MockVM.return_value
        instance.run = AsyncMock(side_effect=RuntimeError("LLM timeout"))

        result = await run_program(store, MINIMAL_PROGRAM)

    assert "error" in result
    assert "Execution failed" in result["error"]
    assert "LLM timeout" in result["error"]


@pytest.mark.asyncio
async def test_run_program_vm_exception_returns_program_id(store):
    """При ошибке vm.run() в ответе присутствует program_id для диагностики."""
    with patch("nano_vm_mcp.tools.ExecutionVM") as MockVM:
        instance = MockVM.return_value
        instance.run = AsyncMock(side_effect=RuntimeError("timeout"))

        result = await run_program(store, MINIMAL_PROGRAM)

    assert "program_id" in result


@pytest.mark.asyncio
async def test_run_program_vm_exception_no_trace_saved(store):
    """При ошибке vm.run() трейс не должен сохраняться в store."""
    with patch("nano_vm_mcp.tools.ExecutionVM") as MockVM:
        instance = MockVM.return_value
        instance.run = AsyncMock(side_effect=RuntimeError("timeout"))

        result = await run_program(store, MINIMAL_PROGRAM)

    assert "trace_id" not in result


# ---------------------------------------------------------------------------
# vm.run() raises — логирование
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_program_vm_exception_is_logged(store, caplog):
    """vm.run() исключение логируется на уровне ERROR с program_id."""
    with patch("nano_vm_mcp.tools.ExecutionVM") as MockVM:
        instance = MockVM.return_value
        instance.run = AsyncMock(side_effect=RuntimeError("network error"))

        with caplog.at_level(logging.ERROR, logger="nano_vm_mcp.tools"):
            await run_program(store, MINIMAL_PROGRAM)

    assert any("vm_run_failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Успешный сценарий не затронут
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_program_success_no_error_key(store):
    """Успешный запуск: error=None, trace_id присутствует."""
    fake_trace = MagicMock()
    fake_trace.status = "COMPLETED"
    fake_trace.steps = []
    fake_trace.total_cost_usd = MagicMock(return_value=0.0)
    fake_trace.model_dump = MagicMock(return_value={"status": "COMPLETED", "steps": []})

    with patch("nano_vm_mcp.tools.ExecutionVM") as MockVM:
        instance = MockVM.return_value
        instance.run = AsyncMock(return_value=fake_trace)

        result = await run_program(store, MINIMAL_PROGRAM)

    assert result["error"] is None
    assert "trace_id" in result
