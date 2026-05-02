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

    async def handle(self, name: str, arguments: dict[str, Any], store: ProgramStore) -> list[TextContent]:
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
# Factory
# ---------------------------------------------------------------------------


def build_chain() -> ToolHandler:
    """Construct and return the head of the tool-dispatch chain."""
    head = RunProgramHandler()
    head.set_successor(GetTraceHandler()) \
        .set_successor(ListProgramsHandler()) \
        .set_successor(GetProgramHandler()) \
        .set_successor(DeleteProgramHandler()) \
        .set_successor(UnknownToolHandler())
    return head
