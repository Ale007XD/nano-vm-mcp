"""CC-06, CC-07 [G-29]: SQLiteCursorRepository.delete возвращает bool; два воркера на
одном sqlite-файле исполняют шаг после SUSPENDED один раз (перенос CL-03 из S0).

Требует ядро llm-nano-vm с атомарным claim (>=0.8.13): со старым ядром результат
delete() игнорируется и CC-07 красный.
"""

from __future__ import annotations

import asyncio
import threading

from nano_vm import ExecutionVM, Program, TraceStatus, WebhookEvent

from nano_vm_mcp.store import ProgramStore
from nano_vm_mcp.vmstep import SQLiteCursorRepository

_PAY = {
    "name": "pay",
    "steps": [
        {"id": "init", "type": "tool", "tool": "initiate"},
        {"id": "cap", "type": "tool", "tool": "capture", "is_terminal": True},
    ],
}


def _tools(charges: list[int]):
    async def initiate(**k):
        return "PENDING"

    async def capture(**k):
        charges.append(1)
        await asyncio.sleep(0.05)
        return "captured"

    return {"initiate": initiate, "capture": capture}


def test_cc06_sqlite_delete_reports_claim(tmp_path):
    """CC-06: первый delete -> True, повторный -> False."""
    repo = SQLiteCursorRepository(ProgramStore(str(tmp_path / "c.db")))

    async def scenario():
        trace = await ExecutionVM(tools=_tools([]), cursor_repository=repo).run(
            Program.from_dict(_PAY)
        )
        assert trace.status == TraceStatus.SUSPENDED
        return await repo.delete(trace.trace_id), await repo.delete(trace.trace_id)

    assert asyncio.run(scenario()) == (True, False)


def test_cc07_two_workers_resume_same_trace_executes_once(tmp_path):
    """CC-07: интерливинг принудительный (барьер после load_vm_cursor)."""
    db = str(tmp_path / "w.db")
    charges: list[int] = []
    tools = _tools(charges)
    prog = Program.from_dict(_PAY)
    trace = asyncio.run(
        ExecutionVM(tools=tools, cursor_repository=SQLiteCursorRepository(ProgramStore(db))).run(
            prog
        )
    )
    assert trace.status == TraceStatus.SUSPENDED
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

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
            outcomes.append("ok")
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert len(charges) == 1, f"capture выполнен {len(charges)} раз(а), ожидалось 1"
    assert sorted(outcomes) == ["ResumeError", "ok"], outcomes
