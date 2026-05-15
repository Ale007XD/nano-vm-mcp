"""nano_vm_mcp.handlers — Chain of Responsibility для MCP tool dispatch.

Каждый ToolHandler отвечает ровно за один инструмент.
Dispatch: handler.handle(name, arguments) → list[TextContent] | None
None означает «не моя зона» — передать следующему в цепочке.

v0.3.0 (Sprint4): GovernanceEnvelope — Pydantic модель (RFC v0.7.0).
  GovernedRunProgramHandler сохраняет envelope в store после каждого запуска.
  Поле canonical_snapshot_hash берётся из Trace.canonical_snapshot_hash().
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from mcp.types import TextContent
from nano_vm.models import PolicySnapshot
from pydantic import BaseModel

from . import tools as _tools
from .store import ProgramStore


def _ok(result: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


# ---------------------------------------------------------------------------
# v0.7.0: GovernanceEnvelope — RFC shared contract (Sprint4)
# ---------------------------------------------------------------------------


class GovernanceEnvelope(BaseModel, frozen=True):
    """
    Обёртка для исходящих MCP-данных после каждого шага lifecycle.

    RFC v0.7.0 fields:
      execution_id           — trace_id / run identifier
      step_id                — порядковый номер шага (0-based)
      policy_hash            — PolicySnapshot.policy_hash на момент шага
      canonical_snapshot_hash — Merkle root из Trace.canonical_snapshot_hash()
      payload                — TRACE-projected payload (dict или list)

    Инварианты:
      - frozen=True: иммутабельна после создания.
      - payload не содержит raw PII: все CapabilityRef заменены на secure_hash().
      - canonical_snapshot_hash детерминирован для одного набора state_snapshots.
    """

    execution_id: str
    step_id: int
    policy_hash: str
    canonical_snapshot_hash: str
    payload: dict[str, Any] | list[Any]


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


# ---------------------------------------------------------------------------
# v0.3.0 / Sprint4: GovernedRunProgramHandler
# ---------------------------------------------------------------------------


class GovernedRunProgramHandler(ToolHandler):
    """
    Расширение RunProgramHandler с capability gate (v0.3.0) и GovernanceEnvelope (Sprint4).

    Lifecycle per RFC nano_vm_mcp_gateway.session_hydration_lifecycle:
      1. Validate tool capabilities against PolicySnapshot (fail-closed).
      2. Run program via _tools.run_program().
      3. Build GovernanceEnvelope с canonical_snapshot_hash из сохранённого trace.
      4. Persist envelope в store.governance_envelopes.
      5. Return result (без envelope в ответе — envelope только для audit/forensics).

    Использование (вместо RunProgramHandler в build_chain):
      head = GovernedRunProgramHandler(policy=snapshot)
    """

    def __init__(self, policy: PolicySnapshot | None = None) -> None:
        super().__init__()
        self._executor = GovernedToolExecutor(policy=policy)
        self._policy = policy

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

    def _build_envelope(
        self,
        trace_id: str,
        step_id: int,
        result: dict[str, Any],
        trace_dict: dict[str, Any] | None,
    ) -> GovernanceEnvelope:
        """
        Строит GovernanceEnvelope из результата run_program и сохранённого trace.

        canonical_snapshot_hash: берётся из trace_dict["state_snapshots"] через
        алгоритм Merkle (дублируем логику Trace.canonical_snapshot_hash() здесь,
        чтобы не тащить Trace-объект через слой store).

        Если trace_dict недоступен (ошибка, не сохранён) — используем пустой hash.
        """
        import hashlib

        policy_hash = self._policy.policy_hash if self._policy else ""

        # Восстанавливаем canonical_snapshot_hash из сохранённого trace
        snapshot_hash: str
        if trace_dict and "state_snapshots" in trace_dict:
            snapshots: list[Any] = trace_dict["state_snapshots"]
            if not snapshots:
                snapshot_hash = hashlib.sha256(b"empty").hexdigest()
            else:
                # Merkle reduction — зеркалим Trace.canonical_snapshot_hash()
                # snapshots: list of [idx, fp_hex] (JSON-сериализованные tuple)
                current_b: list[bytes] = [
                    hashlib.sha256(f"{s[0]}:{s[1]}".encode()).digest() for s in snapshots
                ]
                if len(current_b) == 1:
                    snapshot_hash = current_b[0].hex()
                else:
                    while len(current_b) > 1:
                        if len(current_b) % 2 == 1:
                            current_b.append(current_b[-1])
                        next_b: list[bytes] = []
                        for i in range(0, len(current_b), 2):
                            next_b.append(hashlib.sha256(current_b[i] + current_b[i + 1]).digest())
                        current_b = next_b
                    snapshot_hash = current_b[0].hex()
        else:
            snapshot_hash = hashlib.sha256(b"empty").hexdigest()

        # TRACE-projected payload: статус и шаги без raw PII
        payload: dict[str, Any] = {
            "trace_id": trace_id,
            "status": result.get("status"),
            "steps": result.get("steps", 0),
            "cost": result.get("cost", 0.0),
            "projection_target": "TRACE",
        }

        return GovernanceEnvelope(
            execution_id=trace_id,
            step_id=step_id,
            policy_hash=policy_hash,
            canonical_snapshot_hash=snapshot_hash,
            payload=payload,
        )

    async def _try_handle(
        self, name: str, arguments: dict[str, Any], store: ProgramStore
    ) -> list[TextContent] | None:
        if name != "run_program":
            return None

        program_data = arguments["program"]
        tool_names = self._collect_tools(program_data)

        # 1. Capability gate — fail-closed
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

        # 2. Запуск программы
        result = await _tools.run_program(
            store,
            program_data,
            arguments.get("save_as", ""),
        )

        trace_id: str | None = result.get("trace_id") if isinstance(result, dict) else None

        # 3. TRACE projection + state_context (pre-Sprint4 compat)
        if trace_id:
            store.save_state_context(
                trace_id,
                {
                    "trace_id": trace_id,
                    "status": result.get("status"),
                    "steps_count": result.get("steps", 0),
                    "projection_target": "TRACE",
                },
            )

        # 4. GovernanceEnvelope — строим и сохраняем (Sprint4)
        if trace_id and not result.get("error"):
            trace_dict = store.get_trace(trace_id)
            steps_count: int = result.get("steps", 0)
            # step_id в envelope = индекс последнего шага (steps_count - 1), минимум 0
            envelope_step_id = max(steps_count - 1, 0)

            envelope = self._build_envelope(
                trace_id=trace_id,
                step_id=envelope_step_id,
                result=result,
                trace_dict=trace_dict,
            )
            store.save_envelope(
                execution_id=envelope.execution_id,
                step_id=envelope.step_id,
                policy_hash=envelope.policy_hash,
                snapshot_hash=envelope.canonical_snapshot_hash,
                payload=dict(envelope.payload)
                if isinstance(envelope.payload, dict)
                else list(envelope.payload),
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
