"""S0 repro pack — nano-vm-mcp, аудит 2026-11-09, база b467d56 (ядро llm-nano-vm 0.8.12).

RED = желаемое свойство не выполняется на b467d56; CH = характеризация текущего поведения (GREEN).
Размещение: tools/s0_repro/ (вне testpaths). Запуск из корня репо:
    python -m pytest tools/s0_repro/test_s0_audit_20261109_mcp.py -q -p no:cacheprovider
Ожидание на b467d56: RED = IK-01, IK-02, CL-03; остальные GREEN.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import Any

import pytest
from nano_vm import ExecutionVM, Program, WebhookEvent
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import nano_vm_mcp.tools as _tools_module
from nano_vm_mcp.handlers import GovernedRunProgramHandler
from nano_vm_mcp.server import BearerAuthMiddleware
from nano_vm_mcp.store import ProgramStore
from nano_vm_mcp.vmstep import SQLiteCursorRepository

_PROGRAM = {"steps": [{"id": "s1", "type": "tool", "tool": "noop"}]}


def _seed(store: ProgramStore, trace_id: str) -> None:
    """FK: programs(id) и traces должны существовать (как в test_sprint4_gateway)."""
    store.save_program("prog-1", "test-prog", {"steps": []})
    store.save_trace(
        trace_id=trace_id,
        program_id="prog-1",
        status="success",
        steps_count=1,
        total_cost=0.0,
        trace={"state_snapshots": [[0, "abc"]], "status": "success", "steps": []},
    )


def _fake_run(calls: list[int], delay: float, trace_id: str, error: str | None = None):
    async def _fake(store, program_data, save_as=""):
        calls.append(1)
        await asyncio.sleep(delay)
        if error:
            return {"trace_id": None, "status": "failed", "steps": 0, "cost": 0.0, "error": error}
        return {
            "trace_id": trace_id,
            "program_id": "prog-1",
            "status": "success",
            "steps": 1,
            "cost": 0.0,
            "error": None,
        }

    return _fake


async def _call(
    handler: GovernedRunProgramHandler, store: ProgramStore, key: str
) -> dict[str, Any]:
    out = await handler.handle("run_program", {"program": _PROGRAM, "idempotency_key": key}, store)
    return json.loads(out[0].text)


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = ProgramStore(str(tmp_path / "t.db"))
    trace_id = str(uuid.uuid4())
    _seed(store, trace_id)
    original = _tools_module.run_program
    yield store, trace_id, monkeypatch
    _tools_module.run_program = original


# ───────────────────────── G-30 · idempotency_key ─────────────────────────


def test_ik01_concurrent_same_key_executes_once(env):
    """IK-01 [G-30, RED]: два одновременных run_program с одним idempotency_key →
    программа исполняется один раз (второй ждёт результата либо получает отказ in-flight)."""
    store, trace_id, _ = env
    calls: list[int] = []
    _tools_module.run_program = _fake_run(calls, 0.05, trace_id)
    handler = GovernedRunProgramHandler(policy=None)

    async def scenario():
        await asyncio.gather(_call(handler, store, "K1"), _call(handler, store, "K1"))

    asyncio.run(scenario())
    assert len(calls) == 1, f"программа исполнена {len(calls)} раз(а) при одном ключе"


def test_ik02_failed_run_does_not_leave_key_pending_forever(env):
    """IK-02 [G-30, RED]: после завершения с ошибкой ключ не остаётся 'pending'
    (иначе 'в полёте' неотличимо от 'упало')."""
    store, trace_id, _ = env
    calls: list[int] = []
    _tools_module.run_program = _fake_run(calls, 0.0, trace_id, error="boom")
    handler = GovernedRunProgramHandler(policy=None)
    asyncio.run(_call(handler, store, "K2"))
    row = store.get_idempotency_key("K2")
    assert row is None or row["status"] != "pending", f"ключ завис: {row}"


def test_ik03_sequential_duplicate_returns_cached_characterization(env):
    """IK-03 [G-30, CH]: последовательный повтор после success отдаёт кэш без повторного
    исполнения — дефект только в конкурентном окне."""
    store, trace_id, _ = env
    calls: list[int] = []
    _tools_module.run_program = _fake_run(calls, 0.0, trace_id)
    handler = GovernedRunProgramHandler(policy=None)

    async def scenario():
        a = await _call(handler, store, "K3")
        b = await _call(handler, store, "K3")
        return a, b

    a, b = asyncio.run(scenario())
    assert len(calls) == 1 and a == b


# ───────────────────────── G-29 · claim курсора между воркерами ─────────────────────────

_PAY = {
    "name": "pay",
    "steps": [
        {"id": "init", "type": "tool", "tool": "initiate"},
        {"id": "cap", "type": "tool", "tool": "capture", "is_terminal": True},
    ],
}


def test_cl03_two_workers_resume_same_trace_executes_once(tmp_path):
    """CL-03 [G-29, RED]: два воркера (отдельные соединения к одному sqlite-файлу) получают
    дубль вебхука на один trace_id → шаг после SUSPENDED выполняется ровно один раз.
    Интерливинг принудительный: барьер после чтения курсора (хук на store.load_vm_cursor);
    если фикс переведёт claim на другой метод, барьер не срабатывает по таймауту и тест
    проверяет итог (ровно одно исполнение)."""
    db = str(tmp_path / "w.db")
    charges: list[int] = []

    async def initiate(**k):
        return "PENDING"

    async def capture(**k):
        charges.append(1)
        await asyncio.sleep(0.05)
        return "captured"

    tools = {"initiate": initiate, "capture": capture}
    prog = Program.from_dict(_PAY)
    trace = asyncio.run(
        ExecutionVM(tools=tools, cursor_repository=SQLiteCursorRepository(ProgramStore(db))).run(
            prog
        )
    )
    assert trace.status.name == "SUSPENDED"

    barrier = threading.Barrier(2)

    def worker() -> None:
        store = ProgramStore(db)
        orig = store.load_vm_cursor

        def hooked(trace_id: str):
            row = orig(trace_id)
            try:
                barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            return row

        store.load_vm_cursor = hooked  # type: ignore[method-assign]
        vm = ExecutionVM(tools=tools, cursor_repository=SQLiteCursorRepository(store))
        try:
            asyncio.run(
                vm.resume_with_program(WebhookEvent(trace_id=trace.trace_id, payload={}), prog)
            )
        except Exception:
            pass

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert len(charges) == 1, f"capture выполнен {len(charges)} раз(а), ожидалось 1"


# ───────────────────────── G-23 · auth ─────────────────────────


def _app() -> Starlette:
    async def stub(request):
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[Route("/sse", endpoint=stub)], middleware=[Middleware(BearerAuthMiddleware)]
    )


def test_au01_no_api_key_means_no_auth_characterization(monkeypatch):
    """AU-01 [G-23, CH]: без NANO_VM_MCP_API_KEY middleware пропускает любой запрос (fail-open).
    Меняется осознанно, если сервер перейдёт на fail-closed."""
    monkeypatch.delenv("NANO_VM_MCP_API_KEY", raising=False)
    assert TestClient(_app()).get("/sse").status_code == 200


def test_au02_api_key_set_requires_bearer_characterization(monkeypatch):
    """AU-02 [G-23, CH]: с ключом без заголовка → 401, с верным Bearer → 200."""
    monkeypatch.setenv("NANO_VM_MCP_API_KEY", "k")
    c = TestClient(_app())
    assert c.get("/sse").status_code == 401
    assert c.get("/sse", headers={"Authorization": "Bearer k"}).status_code == 200
