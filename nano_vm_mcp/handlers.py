"""nano_vm_mcp.handlers — Chain of Responsibility для MCP tool dispatch.

Каждый ToolHandler отвечает ровно за один инструмент.
Dispatch: handler.handle(name, arguments) → list[TextContent] | None
None означает «не моя зона» — передать следующему в цепочке.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from mcp.types import TextContent
from nano_vm.models import PolicySnapshot

from . import tools as _tools
from .store import ProgramStore


def _ok(result: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class ToolHandler(ABC):
    """Abstract base for a single-tool handler in the chain."""

    def __init__(self) -> None:
        self._successor: ToolHandler | None = None

    def set_successor(self, successor: ToolHandler) -> ToolHandler:
        """Chain-builder helper; returns successor for fluent chaining."""
        self._successor = successor
        return successor

    async def handle(
        self, name: str, arguments: dict[str, Any], store: ProgramStore
    ) -> list[TextContent]:
        result = await self._try_handle(name, arguments, store)
        if result is not None:
            return result
        if self._successor is not None:
            return await self._successor.handle(name, arguments, store)
        # Terminal: unknown tool (reached only if chain has no UnknownToolHandler)
        return _ok({"error": f"Unknown tool: {name}"})

    @abstractmethod
    async def _try_handle(
        self, name: str, arguments: dict[str, Any], store: ProgramStore
    ) -> list[TextContent] | None:
        """Return result if this handler owns `name`, else None."""


# ---------------------------------------------------------------------------
# Concrete handlers
# ---------------------------------------------------------------------------


class RunProgramHandler(ToolHandler):
    async def _try_handle(
        self, name: str, arguments: dict[str, Any], store: ProgramStore
    ) -> list[TextContent] | None:
        if name != "run_program":
            return None
        result = await _tools.run_program(
            store,
            arguments["program"],
            arguments.get("save_as", ""),
        )
        return _ok(result)


class GetTraceHandler(ToolHandler):
    async def _try_handle(
        self, name: str, arguments: dict[str, Any], store: ProgramStore
    ) -> list[TextContent] | None:
        if name != "get_trace":
            return None
        return _ok(await _tools.get_trace(store, arguments["trace_id"]))


class ListProgramsHandler(ToolHandler):
    async def _try_handle(
        self, name: str, arguments: dict[str, Any], store: ProgramStore
    ) -> list[TextContent] | None:
        if name != "list_programs":
            return None
        return _ok(await _tools.list_programs(store))


class GetProgramHandler(ToolHandler):
    async def _try_handle(
        self, name: str, arguments: dict[str, Any], store: ProgramStore
    ) -> list[TextContent] | None:
        if name != "get_program":
            return None
        return _ok(await _tools.get_program(store, arguments["program_id"]))


class DeleteProgramHandler(ToolHandler):
    async def _try_handle(
        self, name: str, arguments: dict[str, Any], store: ProgramStore
    ) -> list[TextContent] | None:
        if name != "delete_program":
            return None
        return _ok(await _tools.delete_program(store, arguments["program_id"]))


class UnknownToolHandler(ToolHandler):
    """Terminal handler: always matches, returns error for unregistered tools."""

    async def _try_handle(
        self, name: str, arguments: dict[str, Any], store: ProgramStore
    ) -> list[TextContent] | None:
        return _ok({"error": f"Unknown tool: {name}"})


# ---------------------------------------------------------------------------
# v0.3.0: GovernedToolExecutor — capability verification vs PolicySnapshot
# ---------------------------------------------------------------------------


class CapabilityDeniedError(Exception):
    """Вызывается GovernedToolExecutor когда tool не имеет требуемой capability."""


class GovernedToolExecutor:
    """
    Детерминированный gate: проверяет capabilities tool-шага против PolicySnapshot.

    Инварианты:
      - Pure function: нет I/O, нет side effects.
      - Если policy is None → allow (backward compat с pre-v0.3.0 клиентами).
      - tool_name не в policy.tool_capabilities → deny (fail-closed).
      - required_capabilities пусто → allow (tool зарегистрирован, но без ограничений).
      - Все проверки детерминированы: одинаковый (tool_name, policy) → одинаковый результат.

    Использование:
      executor = GovernedToolExecutor(policy=snapshot)
      executor.check("send_email", required=["email.send"])  # OK или CapabilityDeniedError
    """

    def __init__(self, policy: PolicySnapshot | None = None) -> None:
        self._policy = policy

    def check(self, tool_name: str, required: list[str] | None = None) -> None:
        """
        Проверяет наличие capabilities для tool_name.

        Args:
            tool_name: имя инструмента.
            required:  список capability, которые должны быть разрешены.
                       Если None/[] — проверяет только что tool в policy.

        Raises:
            CapabilityDeniedError: если tool не разрешён или capability отсутствует.
        """
        if self._policy is None:
            return  # backward compat: без политики — всё разрешено

        allowed_tools = self._policy.allowed_tools()

        if tool_name not in allowed_tools:
            raise CapabilityDeniedError(
                f"Tool '{tool_name}' is not in policy '{self._policy.policy_id}' "
                f"(allowed: {sorted(allowed_tools)})"
            )

        if not required:
            return  # tool зарегистрирован, конкретных capabilities не требуется

        for cap in required:
            if not self._policy.has_capability(tool_name, cap):
                tool_caps = self._policy.tool_capabilities.get(tool_name, [])
                raise CapabilityDeniedError(
                    f"Tool '{tool_name}' lacks capability '{cap}' "
                    f"(policy '{self._policy.policy_id}', "
                    f"allowed caps: {sorted(tool_caps)})"
                )

    def is_allowed(self, tool_name: str, required: list[str] | None = None) -> bool:
        """Возвращает bool вместо raise. Удобен для условных проверок."""
        try:
            self.check(tool_name, required)
            return True
        except CapabilityDeniedError:
            return False


class GovernedRunProgramHandler(ToolHandler):
    """
    Расширение RunProgramHandler с capability gate (v0.3.0).

    Перед запуском программы проверяет все tool-шаги против PolicySnapshot.
    Если хотя бы один tool не разрешён → возвращает ошибку без запуска VM.

    Использование (вместо RunProgramHandler в build_chain):
      head = GovernedRunProgramHandler(policy=snapshot)
    """

    def __init__(self, policy: PolicySnapshot | None = None) -> None:
        super().__init__()
        self._executor = GovernedToolExecutor(policy=policy)

    def _collect_tools(self, program_data: dict[str, Any]) -> list[str]:
        """Собирает все tool-имена из шагов программы (включая parallel sub-steps)."""
        tools: list[str] = []

        def _scan(steps: list[dict[str, Any]]) -> None:
            for step in steps:
                if step.get("type") == "tool" and step.get("tool"):
                    tools.append(step["tool"])
                if step.get("type") == "parallel":
                    _scan(step.get("parallel_steps", []))

        _scan(program_data.get("steps", []))
        return tools

    async def _try_handle(
        self, name: str, arguments: dict[str, Any], store: ProgramStore
    ) -> list[TextContent] | None:
        if name != "run_program":
            return None

        program_data = arguments["program"]
        tool_names = self._collect_tools(program_data)

        denied: list[str] = []
        for tool_name in tool_names:
            if not self._executor.is_allowed(tool_name):
                denied.append(tool_name)

        if denied:
            return _ok(
                {
                    "error": "capability_denied",
                    "denied_tools": denied,
                    "detail": (
                        f"Tool(s) {denied} not permitted by active policy. "
                        "Update PolicySnapshot.tool_capabilities to allow them."
                    ),
                }
            )

        result = await _tools.run_program(
            store,
            program_data,
            arguments.get("save_as", ""),
        )
        return _ok(result)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_chain() -> ToolHandler:
    """Construct and return the head of the tool-dispatch chain."""
    head = RunProgramHandler()
    head.set_successor(GetTraceHandler()).set_successor(ListProgramsHandler()).set_successor(
        GetProgramHandler()
    ).set_successor(DeleteProgramHandler()).set_successor(UnknownToolHandler())
    return head
