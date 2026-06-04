"""
tests.test_te_transition_stats
================================
sprint_6_core_te: transition_stats table + transition_entropy

TE-01  upsert_transition: новая запись создаётся
TE-02  upsert_transition: повторный вызов инкрементирует count
TE-03  upsert_transition: разные model_id — независимые записи
TE-04  get_transitions: фильтр по program_name
TE-05  get_transitions: фильтр по model_id
TE-06  get_transitions: пустой результат для неизвестного program_name
TE-07  transition_entropy: детерминированный pipeline → 0.0
TE-08  transition_entropy: равновероятные переходы → log2(N)
TE-09  transition_entropy: один шаг → 0.0
TE-10  transition_entropy: alert срабатывает при H > 1.5
TE-11  transition_entropy: alert не срабатывает при H <= 1.5
TE-12  TraceHealthReport.summary() содержит transition_entropy
TE-13  tools.run_program: upsert_transition вызывается для каждого перехода
TE-14  tools.run_program: программа из 1 шага — upsert не вызывается
"""

from __future__ import annotations

import math
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Store tests (TE-01..TE-06)
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_store():
    from nano_vm_mcp.store import ProgramStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    store = ProgramStore(db_path)
    yield store
    store.close()


class TestTransitionStatsStore:
    def test_upsert_creates_record(self, tmp_store):
        """TE-01: новая запись создаётся."""
        tmp_store.upsert_transition("payment_flow", "reserve", "capture")
        rows = tmp_store.get_transitions("payment_flow")
        assert len(rows) == 1
        assert rows[0]["from_step"] == "reserve"
        assert rows[0]["to_step"] == "capture"
        assert rows[0]["count"] == 1

    def test_upsert_increments_count(self, tmp_store):
        """TE-02: повторный вызов инкрементирует count."""
        tmp_store.upsert_transition("payment_flow", "reserve", "capture")
        tmp_store.upsert_transition("payment_flow", "reserve", "capture")
        tmp_store.upsert_transition("payment_flow", "reserve", "capture")
        rows = tmp_store.get_transitions("payment_flow")
        assert rows[0]["count"] == 3

    def test_upsert_different_model_ids_independent(self, tmp_store):
        """TE-03: разные model_id — независимые записи."""
        tmp_store.upsert_transition("flow", "a", "b", model_id="claude")
        tmp_store.upsert_transition("flow", "a", "b", model_id="gpt-4o")
        rows = tmp_store.get_transitions("flow")
        assert len(rows) == 2
        counts = {r["model_id"]: r["count"] for r in rows}
        assert counts["claude"] == 1
        assert counts["gpt-4o"] == 1

    def test_get_transitions_filter_by_program(self, tmp_store):
        """TE-04: фильтр по program_name."""
        tmp_store.upsert_transition("flow_a", "x", "y")
        tmp_store.upsert_transition("flow_b", "x", "y")
        rows = tmp_store.get_transitions("flow_a")
        assert len(rows) == 1
        assert rows[0]["program_name"] == "flow_a"

    def test_get_transitions_filter_by_model_id(self, tmp_store):
        """TE-05: фильтр по model_id."""
        tmp_store.upsert_transition("flow", "a", "b", model_id="claude")
        tmp_store.upsert_transition("flow", "a", "b", model_id="gpt-4o")
        tmp_store.upsert_transition("flow", "b", "c", model_id="claude")
        rows = tmp_store.get_transitions("flow", model_id="claude")
        assert len(rows) == 2
        assert all(r["model_id"] == "claude" for r in rows)

    def test_get_transitions_unknown_program(self, tmp_store):
        """TE-06: пустой результат для неизвестного program_name."""
        rows = tmp_store.get_transitions("nonexistent")
        assert rows == []


# ---------------------------------------------------------------------------
# Analyzer tests (TE-07..TE-12)
# ---------------------------------------------------------------------------


def _make_trace(step_ids: list[str], program_name: str = "test") -> Any:
    from nano_vm.models import StepResult, StepStatus, Trace

    steps = [StepResult(step_id=sid, status=StepStatus.SUCCESS) for sid in step_ids]
    t = Trace(program_name=program_name)
    for s in steps:
        t = t.add_step(s)
    return t


class TestTransitionEntropy:
    def test_deterministic_pipeline_zero_entropy(self):
        """TE-07: детерминированный pipeline → 0.0 (одна пара повторяется).

        Одна уникальная пара b→b с count=4: p=1.0, H = -1.0*log2(1.0) = 0.0.
        Trace ["a","b","a","b","a","b"] даёт ДВЕ пары (a→b и b→a) → H≈0.97 — неверно.
        """
        from nano_vm.analyzer import TraceAnalyzer

        # step→step × 4: единственная пара, p=1.0, H = -1.0*log2(1.0) = 0.0
        trace = _make_trace(["step", "step", "step", "step", "step"])
        h = TraceAnalyzer(trace).transition_entropy()
        assert h == pytest.approx(0.0, abs=1e-9)

    def test_uniform_transitions_max_entropy(self):
        """TE-08: N равновероятных пар → log2(N)."""
        from nano_vm.analyzer import TraceAnalyzer

        # Переходы: a→b, b→c, c→d, d→e — все уникальные, count=1 каждая
        trace = _make_trace(["a", "b", "c", "d", "e"])
        h = TraceAnalyzer(trace).transition_entropy()
        expected = math.log2(4)  # 4 уникальных пары
        assert h == pytest.approx(expected, abs=1e-9)

    def test_single_step_zero_entropy(self):
        """TE-09: один шаг → 0.0 (нет переходов)."""
        from nano_vm.analyzer import TraceAnalyzer

        trace = _make_trace(["only_step"])
        h = TraceAnalyzer(trace).transition_entropy()
        assert h == 0.0

    def test_empty_trace_zero_entropy(self):
        """TE-09b: пустой trace → 0.0."""
        from nano_vm.analyzer import TraceAnalyzer

        trace = _make_trace([])
        h = TraceAnalyzer(trace).transition_entropy()
        assert h == 0.0

    def test_alert_fires_above_threshold(self):
        """TE-10: alert срабатывает при H > 2.5 (threshold = 2.5 bits)."""
        from nano_vm.analyzer import TraceAnalyzer

        # 8 уникальных переходов → H = log2(8) = 3.0 > 2.5
        trace = _make_trace(["a", "b", "c", "d", "e", "f", "g", "h", "i"])
        report = TraceAnalyzer(trace).report()
        alert_msgs = " ".join(report.alerts)
        assert "transition_entropy" in alert_msgs

    def test_alert_does_not_fire_below_threshold(self):
        """TE-11: alert не срабатывает при H <= 2.5 (threshold = 2.5 bits)."""
        from nano_vm.analyzer import TraceAnalyzer

        # Один переход a→b повторяется → H = 0.0
        trace = _make_trace(["a", "b", "a", "b"])
        report = TraceAnalyzer(trace).report()
        alert_msgs = " ".join(report.alerts)
        assert "transition_entropy" not in alert_msgs

    def test_summary_contains_entropy_field(self):
        """TE-12: TraceHealthReport.summary() содержит transition_entropy."""
        from nano_vm.analyzer import TraceAnalyzer

        trace = _make_trace(["a", "b", "c"])
        report = TraceAnalyzer(trace).report()
        summary = report.summary()
        assert "transition_entropy" in summary


# ---------------------------------------------------------------------------
# tools.run_program wiring (TE-13..TE-14)
# ---------------------------------------------------------------------------


class TestToolsRunProgramWiring:
    @pytest.mark.asyncio
    async def test_upsert_called_for_each_transition(self):
        """TE-13: run_program вызывает upsert_transition для каждого перехода."""
        from nano_vm.models import StepResult, StepStatus, Trace, TraceStatus

        from nano_vm_mcp import tools

        step_ids = ["validate", "reserve", "capture", "receipt"]
        steps = [StepResult(step_id=sid, status=StepStatus.SUCCESS) for sid in step_ids]
        mock_trace = Trace(program_name="payment_flow", status=TraceStatus.SUCCESS)
        for s in steps:
            mock_trace = mock_trace.add_step(s)

        store = MagicMock()
        store.save_program = MagicMock()
        store.save_trace = MagicMock()
        store.upsert_transition = MagicMock()

        program_data = {
            "name": "payment_flow",
            "steps": [{"id": sid, "type": "tool", "tool": "noop"} for sid in step_ids],
        }

        with patch.object(tools, "_build_vm") as mock_build:
            mock_vm = MagicMock()
            mock_vm.run = AsyncMock(return_value=mock_trace)
            mock_build.return_value = mock_vm

            await tools.run_program(store, program_data)

        # 4 шага → 3 перехода
        assert store.upsert_transition.call_count == 3
        calls = [c.kwargs for c in store.upsert_transition.call_args_list]
        assert calls[0]["from_step"] == "validate"
        assert calls[0]["to_step"] == "reserve"
        assert calls[1]["from_step"] == "reserve"
        assert calls[1]["to_step"] == "capture"
        assert calls[2]["from_step"] == "capture"
        assert calls[2]["to_step"] == "receipt"

    @pytest.mark.asyncio
    async def test_upsert_not_called_for_single_step(self):
        """TE-14: программа из 1 шага — upsert не вызывается."""
        from nano_vm.models import StepResult, StepStatus, Trace, TraceStatus

        from nano_vm_mcp import tools

        steps = [StepResult(step_id="only", status=StepStatus.SUCCESS)]
        mock_trace = Trace(program_name="single_step", status=TraceStatus.SUCCESS)
        for s in steps:
            mock_trace = mock_trace.add_step(s)

        store = MagicMock()
        store.save_program = MagicMock()
        store.save_trace = MagicMock()
        store.upsert_transition = MagicMock()

        program_data = {
            "name": "single_step",
            "steps": [{"id": "only", "type": "tool", "tool": "noop"}],
        }

        with patch.object(tools, "_build_vm") as mock_build:
            mock_vm = MagicMock()
            mock_vm.run = AsyncMock(return_value=mock_trace)
            mock_build.return_value = mock_vm

            await tools.run_program(store, program_data)

        store.upsert_transition.assert_not_called()
