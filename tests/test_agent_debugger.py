"""
tests.test_agent_debugger
==========================
sprint_v044: Agent Debugger integration — debug_trace MCP tool + auto-diagnostic

AD-01  _build_debugger_payload: маппинг trace_dict → схема Agent Debugger
AD-02  _build_debugger_payload: FAIL:<reason> sentinel пробрасывается as-is
AD-03  call_agent_debugger: успешный вызов возвращает diagnostic dict
AD-04  call_agent_debugger: AGENT_DEBUGGER_TOKEN не установлен → {"error": ...}
AD-05  call_agent_debugger: HTTP ошибка → {"error": ...} без исключения
AD-06  debug_trace tool: trace not found → {"error": "not found"}
AD-07  debug_trace tool: успешный вызов возвращает trace_id + status + diagnostic
AD-08  auto-diagnostic: run_program FAILED + TOKEN установлен → diagnostic в ответе
AD-09  auto-diagnostic: run_program SUCCESS → diagnostic не вызывается
AD-10  auto-diagnostic: run_program FAILED + TOKEN не установлен → diagnostic отсутствует
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trace_dict(
    status: str = "FAILED",
    step_ids: list[str] | None = None,
    program_name: str = "PROGRAM_KYC",
) -> dict[str, Any]:
    if step_ids is None:
        step_ids = ["collect_data", "screen_sanctions"]
    steps = [
        {
            "step_id": sid,
            "type": "tool",
            "status": "FAILED" if i == len(step_ids) - 1 else "SUCCESS",
            "output": "FAIL:timeout" if i == len(step_ids) - 1 else "OK",
            "retry_count": 3 if i == len(step_ids) - 1 else 0,
            "duration_ms": 9800 if i == len(step_ids) - 1 else 120,
        }
        for i, sid in enumerate(step_ids)
    ]
    return {
        "trace_id": "3f8a1c2d-7e4b-4f9a-b2d1-9c0e5f3a8b7d",
        "program_name": program_name,
        "status": status,
        "steps": steps,
    }


def _make_debugger_response() -> dict[str, Any]:
    return {
        "trace_id": "3f8a1c2d-7e4b-4f9a-b2d1-9c0e5f3a8b7d",
        "score": 20,
        "pattern": None,
        "failures": [
            {
                "failure_type": "retry_loop",
                "severity": "critical",
                "failure_point": {"step": 1, "evidence": "screen_sanctions failed 3 times"},
                "likely_cause": {
                    "confirmed": "upstream unavailable, retry storm",
                    "hypothesis": "unknown",
                },
                "suggested_fix": {
                    "quick": "add circuit breaker",
                    "robust": "implement fallback screening provider",
                },
            }
        ],
        "confidence": 0.85,
        "debugging_signals": ["rollback_density > 0.3"],
    }


# ---------------------------------------------------------------------------
# AD-01..02: _build_debugger_payload
# ---------------------------------------------------------------------------


class TestBuildDebuggerPayload:
    def test_maps_trace_fields_correctly(self):
        """AD-01: маппинг trace_dict → схема Agent Debugger."""
        from nano_vm_mcp.tools import _build_debugger_payload

        trace_dict = _make_trace_dict()
        payload = _build_debugger_payload(trace_dict)

        assert payload["trace_id"] == "3f8a1c2d-7e4b-4f9a-b2d1-9c0e5f3a8b7d"
        assert payload["trace"]["program_name"] == "PROGRAM_KYC"
        assert payload["trace"]["status"] == "FAILED"
        assert len(payload["trace"]["steps"]) == 2
        assert payload["trace"]["final_step"] == "screen_sanctions"

        step = payload["trace"]["steps"][1]
        assert step["step_id"] == "screen_sanctions"
        assert step["type"] == "tool"
        assert step["retries"] == 3
        assert step["duration_ms"] == 9800

    def test_fail_sentinel_passthrough(self):
        """AD-02: FAIL:<reason> sentinel пробрасывается as-is в поле output."""
        from nano_vm_mcp.tools import _build_debugger_payload

        trace_dict = _make_trace_dict()
        payload = _build_debugger_payload(trace_dict)

        last_step = payload["trace"]["steps"][-1]
        assert last_step["output"] == "FAIL:timeout"


# ---------------------------------------------------------------------------
# AD-03..05: call_agent_debugger
# ---------------------------------------------------------------------------


class TestCallAgentDebugger:
    @pytest.mark.asyncio
    async def test_successful_call_returns_diagnostic(self):
        """AD-03: успешный вызов возвращает diagnostic dict от Agent Debugger."""
        from nano_vm_mcp import tools

        trace_dict = _make_trace_dict()
        expected = _make_debugger_response()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=expected)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch.object(tools, "AGENT_DEBUGGER_TOKEN", "test-token"),
            patch.object(tools, "_HTTPX_AVAILABLE", True),
            patch("nano_vm_mcp.tools.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await tools.call_agent_debugger(trace_dict)

        assert result["score"] == 20
        assert result["confidence"] == 0.85
        assert result["failures"][0]["failure_type"] == "retry_loop"

        # Verify bearer auth was sent
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_missing_token_returns_error(self):
        """AD-04: AGENT_DEBUGGER_TOKEN не установлен → {"error": ...}."""
        from nano_vm_mcp import tools

        with (
            patch.object(tools, "AGENT_DEBUGGER_TOKEN", ""),
            patch.object(tools, "_HTTPX_AVAILABLE", True),
        ):
            result = await tools.call_agent_debugger(_make_trace_dict())

        assert "error" in result
        assert "AGENT_DEBUGGER_TOKEN" in result["error"]

    @pytest.mark.asyncio
    async def test_http_error_returns_error_dict(self):
        """AD-05: HTTP ошибка → {"error": ...} без исключения."""
        import httpx

        from nano_vm_mcp import tools

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "401", request=MagicMock(), response=mock_response
            )
        )

        with (
            patch.object(tools, "AGENT_DEBUGGER_TOKEN", "bad-token"),
            patch.object(tools, "_HTTPX_AVAILABLE", True),
            patch("nano_vm_mcp.tools.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await tools.call_agent_debugger(_make_trace_dict())

        assert "error" in result
        assert "401" in result["error"]


# ---------------------------------------------------------------------------
# AD-06..07: debug_trace MCP tool
# ---------------------------------------------------------------------------


class TestDebugTraceTool:
    @pytest.mark.asyncio
    async def test_trace_not_found_returns_error(self):
        """AD-06: trace not found → {"error": "Trace '...' not found"}."""
        from nano_vm_mcp import tools

        store = MagicMock()
        store.get_trace = MagicMock(return_value=None)

        result = await tools.debug_trace(store, "nonexistent-id")

        assert "error" in result
        assert "nonexistent-id" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_trace_id_status_diagnostic(self):
        """AD-07: успешный вызов возвращает trace_id + status + diagnostic."""
        from nano_vm_mcp import tools

        trace_dict = _make_trace_dict()
        store = MagicMock()
        store.get_trace = MagicMock(return_value=trace_dict)

        diagnostic = _make_debugger_response()

        with patch.object(tools, "call_agent_debugger", AsyncMock(return_value=diagnostic)):
            result = await tools.debug_trace(store, trace_dict["trace_id"])

        assert result["trace_id"] == trace_dict["trace_id"]
        assert result["status"] == "FAILED"
        assert result["diagnostic"] == diagnostic


# ---------------------------------------------------------------------------
# AD-08..10: auto-diagnostic in GovernedRunProgramHandler
# ---------------------------------------------------------------------------


def _make_mock_store(trace_dict: dict[str, Any] | None = None) -> MagicMock:
    store = MagicMock()
    store.save_program = MagicMock()
    store.save_trace = MagicMock()
    store.upsert_transition = MagicMock()
    store.get_idempotency_key = MagicMock(return_value=None)
    store.save_idempotency_key = MagicMock()
    store.save_state_context = MagicMock()
    store.save_envelope = MagicMock()
    store.save_trace_step = MagicMock()
    store.get_trace = MagicMock(return_value=trace_dict)
    return store


def _make_mock_trace(status: str = "FAILED") -> MagicMock:
    from nano_vm.models import TraceStatus

    ts = TraceStatus.FAILED if status == "FAILED" else TraceStatus.SUCCESS
    mock_trace = MagicMock()
    mock_trace.trace_id = "3f8a1c2d-7e4b-4f9a-b2d1-9c0e5f3a8b7d"
    mock_trace.status = ts
    mock_trace.steps = []
    mock_trace.model_dump = MagicMock(return_value={"status": status, "steps": []})
    mock_trace.total_cost_usd = 0.0
    return mock_trace


MINIMAL_PROGRAM: dict[str, Any] = {
    "name": "test_program",
    "steps": [
        {"id": "step_1", "type": "tool", "tool": "noop", "is_terminal": True}
    ],
}


class TestAutodiagnostic:
    @pytest.mark.asyncio
    async def test_failed_program_with_token_gets_diagnostic(self):
        """AD-08: run_program FAILED + TOKEN установлен → diagnostic в ответе."""
        from nano_vm_mcp import tools
        from nano_vm_mcp.handlers import GovernedRunProgramHandler

        trace_dict = _make_trace_dict(status="FAILED")
        store = _make_mock_store(trace_dict=trace_dict)
        mock_trace = _make_mock_trace("FAILED")
        diagnostic = _make_debugger_response()

        handler = GovernedRunProgramHandler(policy=None)

        with (
            patch.object(tools, "_build_vm") as mock_build,
            patch.object(tools, "call_agent_debugger", AsyncMock(return_value=diagnostic)),
            patch.dict("os.environ", {"AGENT_DEBUGGER_TOKEN": "test-token"}),
        ):
            mock_vm = MagicMock()
            mock_vm.run = AsyncMock(return_value=mock_trace)
            mock_build.return_value = mock_vm

            import json
            result_tc = await handler._try_handle("run_program", {"program": MINIMAL_PROGRAM}, store)

        assert result_tc is not None
        result = json.loads(result_tc[0].text)
        assert "diagnostic" in result
        assert result["diagnostic"]["score"] == 20

    @pytest.mark.asyncio
    async def test_success_program_no_diagnostic(self):
        """AD-09: run_program SUCCESS → call_agent_debugger не вызывается."""
        from nano_vm_mcp import tools
        from nano_vm_mcp.handlers import GovernedRunProgramHandler

        trace_dict = _make_trace_dict(status="SUCCESS")
        store = _make_mock_store(trace_dict=trace_dict)
        mock_trace = _make_mock_trace("SUCCESS")

        handler = GovernedRunProgramHandler(policy=None)
        mock_debugger = AsyncMock()

        with (
            patch.object(tools, "_build_vm") as mock_build,
            patch.object(tools, "call_agent_debugger", mock_debugger),
            patch.dict("os.environ", {"AGENT_DEBUGGER_TOKEN": "test-token"}),
        ):
            mock_vm = MagicMock()
            mock_vm.run = AsyncMock(return_value=mock_trace)
            mock_build.return_value = mock_vm

            import json
            result_tc = await handler._try_handle("run_program", {"program": MINIMAL_PROGRAM}, store)

        mock_debugger.assert_not_called()
        result = json.loads(result_tc[0].text)
        assert "diagnostic" not in result

    @pytest.mark.asyncio
    async def test_failed_program_without_token_no_diagnostic(self):
        """AD-10: run_program FAILED + TOKEN не установлен → diagnostic отсутствует."""
        from nano_vm_mcp import tools
        from nano_vm_mcp.handlers import GovernedRunProgramHandler

        trace_dict = _make_trace_dict(status="FAILED")
        store = _make_mock_store(trace_dict=trace_dict)
        mock_trace = _make_mock_trace("FAILED")

        handler = GovernedRunProgramHandler(policy=None)
        mock_debugger = AsyncMock()

        with (
            patch.object(tools, "_build_vm") as mock_build,
            patch.object(tools, "call_agent_debugger", mock_debugger),
            patch.dict("os.environ", {}, clear=True),
        ):
            mock_vm = MagicMock()
            mock_vm.run = AsyncMock(return_value=mock_trace)
            mock_build.return_value = mock_vm

            import json
            result_tc = await handler._try_handle("run_program", {"program": MINIMAL_PROGRAM}, store)

        mock_debugger.assert_not_called()
        result = json.loads(result_tc[0].text)
        assert "diagnostic" not in result
