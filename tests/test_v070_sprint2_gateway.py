"""
tests.test_v070_sprint2_gateway
================================
Sprint 2: GovernedToolExecutor + GovernedRunProgramHandler

ЭТОТ ФАЙЛ ПРИНАДЛЕЖИТ РЕПО nano_vm_mcp, НЕ nano_vm.
Перенести в: nano_vm_mcp/tests/test_v070_sprint2_gateway.py

Покрытие:
  - GovernedToolExecutor: allow/deny по policy + capability
  - GovernedRunProgramHandler: pre-flight check до запуска VM
  - parallel steps: все sub-steps проверяются
"""

from __future__ import annotations

# ЭТОТ ФАЙЛ НЕ ВХОДИТ В CI РЕПО nano_vm.
# Он здесь только как артефакт для переноса.
# Запускать только из репо nano_vm_mcp где установлен пакет.
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nano_vm.models import PolicySnapshot

# Эти импорты сработают только в репо nano_vm_mcp:
from nano_vm_mcp.handlers import (
    CapabilityDeniedError,
    GovernedRunProgramHandler,
    GovernedToolExecutor,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def policy() -> PolicySnapshot:
    return PolicySnapshot(
        policy_id="p-test",
        version="1.0.0",
        tool_capabilities={
            "send_email": ["email.read_raw", "email.send"],
            "get_weather": ["weather.read"],
            "save_report": ["report.write"],
        },
    )


# ---------------------------------------------------------------------------
# GovernedToolExecutor
# ---------------------------------------------------------------------------


class TestGovernedToolExecutor:
    def test_allow_tool_with_capability(self, policy: PolicySnapshot) -> None:
        executor = GovernedToolExecutor(policy=policy)
        executor.check("send_email", ["email.send"])

    def test_allow_tool_all_capabilities(self, policy: PolicySnapshot) -> None:
        executor = GovernedToolExecutor(policy=policy)
        executor.check("send_email", ["email.read_raw", "email.send"])

    def test_deny_tool_not_in_policy(self, policy: PolicySnapshot) -> None:
        executor = GovernedToolExecutor(policy=policy)
        with pytest.raises(CapabilityDeniedError) as exc_info:
            executor.check("exec_shell")
        assert "exec_shell" in str(exc_info.value)
        assert "p-test" in str(exc_info.value)

    def test_deny_missing_capability(self, policy: PolicySnapshot) -> None:
        executor = GovernedToolExecutor(policy=policy)
        with pytest.raises(CapabilityDeniedError) as exc_info:
            executor.check("send_email", ["email.delete"])
        assert "email.delete" in str(exc_info.value)

    def test_allow_no_policy(self) -> None:
        executor = GovernedToolExecutor(policy=None)
        executor.check("any_tool", ["any.capability"])

    def test_allow_tool_no_required_capabilities(self, policy: PolicySnapshot) -> None:
        executor = GovernedToolExecutor(policy=policy)
        executor.check("send_email", [])
        executor.check("send_email", None)  # type: ignore[arg-type]

    def test_is_allowed_true(self, policy: PolicySnapshot) -> None:
        executor = GovernedToolExecutor(policy=policy)
        assert executor.is_allowed("get_weather", ["weather.read"]) is True

    def test_is_allowed_false_tool(self, policy: PolicySnapshot) -> None:
        executor = GovernedToolExecutor(policy=policy)
        assert executor.is_allowed("rm_rf") is False

    def test_is_allowed_false_capability(self, policy: PolicySnapshot) -> None:
        executor = GovernedToolExecutor(policy=policy)
        assert executor.is_allowed("send_email", ["email.admin"]) is False

    def test_error_message_contains_allowed_caps(self, policy: PolicySnapshot) -> None:
        executor = GovernedToolExecutor(policy=policy)
        with pytest.raises(CapabilityDeniedError) as exc_info:
            executor.check("send_email", ["email.delete"])
        msg = str(exc_info.value)
        assert "email.read_raw" in msg or "email.send" in msg


# ---------------------------------------------------------------------------
# GovernedRunProgramHandler
# ---------------------------------------------------------------------------


def _make_program_data(tool_names: list[str]) -> dict[str, Any]:
    return {
        "steps": [
            {"id": f"s{i}", "type": "tool", "tool": name} for i, name in enumerate(tool_names)
        ]
    }


class TestGovernedRunProgramHandler:
    @pytest.mark.asyncio
    async def test_allow_permitted_tools(self, policy: PolicySnapshot) -> None:
        handler = GovernedRunProgramHandler(policy=policy)
        store = MagicMock()
        program_data = _make_program_data(["send_email", "get_weather"])

        with patch("nano_vm_mcp.handlers._tools.run_program", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"trace_id": "t1", "status": "SUCCESS"}
            result = await handler._try_handle("run_program", {"program": program_data}, store)

        assert result is not None
        payload = json.loads(result[0].text)
        assert "error" not in payload or payload.get("error") is None
        mock_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deny_unpermitted_tool(self, policy: PolicySnapshot) -> None:
        handler = GovernedRunProgramHandler(policy=policy)
        store = MagicMock()
        program_data = _make_program_data(["send_email", "exec_shell"])

        with patch("nano_vm_mcp.handlers._tools.run_program", new_callable=AsyncMock) as mock_run:
            result = await handler._try_handle("run_program", {"program": program_data}, store)

        assert result is not None
        payload = json.loads(result[0].text)
        assert payload["error"] == "capability_denied"
        assert "exec_shell" in payload["denied_tools"]
        mock_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_policy_allows_all(self) -> None:
        handler = GovernedRunProgramHandler(policy=None)
        store = MagicMock()
        program_data = _make_program_data(["any_tool", "another_tool"])

        with patch("nano_vm_mcp.handlers._tools.run_program", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"trace_id": "t2", "status": "SUCCESS"}
            result = await handler._try_handle("run_program", {"program": program_data}, store)

        mock_run.assert_awaited_once()
        assert result is not None

    @pytest.mark.asyncio
    async def test_unknown_tool_name_returns_none(self, policy: PolicySnapshot) -> None:
        handler = GovernedRunProgramHandler(policy=policy)
        store = MagicMock()
        result = await handler._try_handle("get_trace", {"trace_id": "x"}, store)
        assert result is None

    @pytest.mark.asyncio
    async def test_parallel_tools_checked(self, policy: PolicySnapshot) -> None:
        handler = GovernedRunProgramHandler(policy=policy)
        store = MagicMock()
        program_data = {
            "steps": [
                {
                    "id": "p1",
                    "type": "parallel",
                    "parallel_steps": [
                        {"id": "s1", "type": "tool", "tool": "get_weather"},
                        {"id": "s2", "type": "tool", "tool": "forbidden_tool"},
                    ],
                }
            ]
        }

        with patch("nano_vm_mcp.handlers._tools.run_program", new_callable=AsyncMock) as mock_run:
            result = await handler._try_handle("run_program", {"program": program_data}, store)

        payload = json.loads(result[0].text)  # type: ignore[index]
        assert payload["error"] == "capability_denied"
        assert "forbidden_tool" in payload["denied_tools"]
        mock_run.assert_not_awaited()
