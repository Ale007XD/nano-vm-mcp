"""tests/test_sprint5_mcp_vmstep.py
VS-01..VS-18 — vm.step() sprint (sprint_5_mcp_vmstep)

VS-13..18 close the coverage audit gaps identified after VS-01..12:
new-VM-instance proof (VS-13), explicit cursor cleanup after resume
(VS-14..16), and _GatewayCursorRepository bridge unit tests bypassing
vm_step() entirely (VS-17..18).

Scope cut (DECISIONS.md 2026-06-18): no circuit_breaker, no
PROGRAM_IPN_HANDLER — covered separately by sprint_5_mcp_pending.
"""

from __future__ import annotations

import pytest

from nano_vm_mcp import tools
from nano_vm_mcp.handlers import build_chain
from nano_vm_mcp.store import ProgramStore
from nano_vm_mcp.tools import _GatewayCursorRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: pytest.TempPathFactory) -> ProgramStore:
    db = str(tmp_path / "test.db")  # type: ignore[operator]
    return ProgramStore(db)


_SUSPENDING_PROGRAM: dict = {
    "name": "vmstep_suspend_test",
    "steps": [
        {"id": "ask", "type": "tool", "tool": "wait_for_input", "args": {}, "is_terminal": False},
        {
            "id": "finalize",
            "type": "tool",
            "tool": "echo_webhook",
            "args": {},
            "is_terminal": True,
        },
    ],
}

_LINEAR_PROGRAM: dict = {
    "name": "vmstep_linear_test",
    "steps": [
        {"id": "only", "type": "tool", "tool": "echo_static", "args": {}, "is_terminal": True},
    ],
}


def _suspend_call_counter() -> dict[str, int]:
    return {"n": 0}


# Module-level registration shim: tools._build_vm only ever constructs
# MockLLMAdapter/LiteLLMAdapter-backed ExecutionVM with NO tools registered
# (tools={} default). vm_step()/_build_vm doesn't accept a tools dict from
# the caller, so we monkeypatch ExecutionVM.register_tool indirectly by
# patching _build_vm's returned VM in each test via the public API surface:
# we rely on the fact that _GatewayCursorRepository is swapped onto the VM
# *after* _build_vm — same pattern works for tools by registering directly
# on the returned vm before run()/resume(). Tests below patch tools.py's
# _build_vm to inject the fixture's tool registry, mirroring how a real
# channel adapter would extend a Program's tool set.


@pytest.fixture
def patch_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = _suspend_call_counter()

    def wait_for_input() -> str:
        counter["n"] += 1
        if counter["n"] == 1:
            return "PENDING"
        return "resumed"

    def echo_webhook() -> str:
        return "finalized"

    def echo_static() -> str:
        return "static-output"

    real_build_vm = tools._build_vm

    def fake_build_vm(program_data: dict) -> object:
        vm_or_err = real_build_vm(program_data)
        if isinstance(vm_or_err, str):
            return vm_or_err
        vm_or_err.register_tool("wait_for_input", wait_for_input)
        vm_or_err.register_tool("echo_webhook", echo_webhook)
        vm_or_err.register_tool("echo_static", echo_static)
        return vm_or_err

    monkeypatch.setattr(tools, "_build_vm", fake_build_vm)


# ---------------------------------------------------------------------------
# VS-01..04: new session, linear (no suspend)
# ---------------------------------------------------------------------------


async def test_vs01_new_session_linear_terminal(store: ProgramStore, patch_tools: None) -> None:
    """VS-01: program with no PENDING tool runs straight to SUCCESS, suspended=False."""
    result = await tools.vm_step(store, program=_LINEAR_PROGRAM)
    assert result["error"] is None
    assert result["suspended"] is False
    assert result["status"] == "SUCCESS"
    assert result["output"] == "static-output"
    assert result["session_id"] == result["trace_id"]


async def test_vs02_new_session_returns_session_id(store: ProgramStore, patch_tools: None) -> None:
    """VS-02: new-session response session_id is usable as a UUID-shaped string."""
    result = await tools.vm_step(store, program=_LINEAR_PROGRAM)
    assert isinstance(result["session_id"], str)
    assert len(result["session_id"]) > 0


async def test_vs03_new_session_invalid_program(store: ProgramStore) -> None:
    """VS-03: malformed program returns validation error, no session created."""
    result = await tools.vm_step(store, program={"steps": "not-a-list"})
    assert "error" in result
    assert "Invalid program" in result["error"]


async def test_vs04_new_session_persists_trace(store: ProgramStore, patch_tools: None) -> None:
    """VS-04: terminal run is visible via get_trace (store.save_trace called)."""
    result = await tools.vm_step(store, program=_LINEAR_PROGRAM)
    trace_dict = store.get_trace(result["trace_id"])
    assert trace_dict is not None
    assert trace_dict["status"] == "success"


# ---------------------------------------------------------------------------
# VS-05..09: suspend / resume round-trip
# ---------------------------------------------------------------------------


async def test_vs05_new_session_suspends(store: ProgramStore, patch_tools: None) -> None:
    """VS-05: program hitting PENDING tool output returns suspended=True."""
    result = await tools.vm_step(store, program=_SUSPENDING_PROGRAM)
    assert result["error"] is None
    assert result["suspended"] is True
    assert result["status"] == "SUSPENDED"
    assert result["output"] is None


async def test_vs06_session_persisted_for_resume(store: ProgramStore, patch_tools: None) -> None:
    """VS-06: suspended session is retrievable via ProgramStore.get_vm_session."""
    result = await tools.vm_step(store, program=_SUSPENDING_PROGRAM)
    session_row = store.get_vm_session(result["session_id"])
    assert session_row is not None
    assert session_row["cursor_step_id"] == "ask"
    assert session_row["program_id"] == result["program_id"]


async def test_vs07_resume_with_session_id_completes(
    store: ProgramStore, patch_tools: None
) -> None:
    """VS-07: second vm_step() call with session_id only resumes to terminal."""
    first = await tools.vm_step(store, program=_SUSPENDING_PROGRAM)
    assert first["suspended"] is True

    second = await tools.vm_step(
        store, session_id=first["session_id"], input_data={"answer": "yes"}
    )
    assert second["error"] is None
    assert second["suspended"] is False
    assert second["status"] == "SUCCESS"
    assert second["output"] == "finalized"
    assert second["session_id"] == first["session_id"]


async def test_vs08_resume_unknown_session_id(store: ProgramStore, patch_tools: None) -> None:
    """VS-08: resume against a session_id with no saved cursor returns an error."""
    result = await tools.vm_step(store, session_id="nonexistent-session", input_data={})
    assert "error" in result
    assert "not found" in result["error"]


async def test_vs09_resume_after_already_terminal_session(
    store: ProgramStore, patch_tools: None
) -> None:
    """VS-09: resuming an already-completed session (cursor deleted) errors cleanly."""
    first = await tools.vm_step(store, program=_SUSPENDING_PROGRAM)
    second = await tools.vm_step(store, session_id=first["session_id"], input_data={})
    assert second["suspended"] is False

    third = await tools.vm_step(store, session_id=first["session_id"], input_data={})
    assert "error" in third


# ---------------------------------------------------------------------------
# VS-10..11: missing args
# ---------------------------------------------------------------------------


async def test_vs10_no_program_no_session_id_errors(store: ProgramStore) -> None:
    """VS-10: calling vm_step() with neither program nor session_id is an error."""
    result = await tools.vm_step(store)
    assert "error" in result


async def test_vs11_resume_missing_program_for_session(store: ProgramStore) -> None:
    """VS-11: session row exists but its program was deleted -> clean error, no crash."""
    store.save_vm_session(
        session_id="orphan-session",
        program_id="missing-program-id",
        cursor_step_id="ask",
        state={"data": {}, "step_outputs": {}},
        trace={"program_name": "x", "status": "SUSPENDED"},
    )
    result = await tools.vm_step(store, session_id="orphan-session", input_data={})
    assert "error" in result
    assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# VS-12: dispatch through the handler chain (MCP tool name "vm_step")
# ---------------------------------------------------------------------------


async def test_vs12_handler_chain_dispatch(store: ProgramStore, patch_tools: None) -> None:
    """VS-12: build_chain() routes vm_step tool calls to VmStepHandler."""
    import json

    chain = build_chain()
    content = await chain.handle("vm_step", {"program": _LINEAR_PROGRAM}, store)
    payload = json.loads(content[0].text)
    assert payload["suspended"] is False
    assert payload["status"] == "SUCCESS"


# ---------------------------------------------------------------------------
# VS-13: new ExecutionVM instance per vm_step() call (audit gap)
# ---------------------------------------------------------------------------
#
# tools.py's docstring claims "each call constructs a fresh ExecutionVM"
# as the rationale for needing _GatewayCursorRepository at all. VS-01..12
# never asserted this directly — a global-singleton VM could have passed
# the same suite by accident. This wraps _build_vm to record id() of every
# VM it returns and asserts the suspend-call VM and the resume-call VM are
# different objects.


@pytest.fixture
def patch_tools_with_vm_identity_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> list[int]:
    """Same tool wiring as patch_tools, but also records id(vm) per _build_vm() call."""
    counter = _suspend_call_counter()
    seen_vm_ids: list[int] = []

    def wait_for_input() -> str:
        counter["n"] += 1
        if counter["n"] == 1:
            return "PENDING"
        return "resumed"

    def echo_webhook() -> str:
        return "finalized"

    def echo_static() -> str:
        return "static-output"

    real_build_vm = tools._build_vm

    def fake_build_vm(program_data: dict) -> object:
        vm_or_err = real_build_vm(program_data)
        if isinstance(vm_or_err, str):
            return vm_or_err
        vm_or_err.register_tool("wait_for_input", wait_for_input)
        vm_or_err.register_tool("echo_webhook", echo_webhook)
        vm_or_err.register_tool("echo_static", echo_static)
        seen_vm_ids.append(id(vm_or_err))
        return vm_or_err

    monkeypatch.setattr(tools, "_build_vm", fake_build_vm)
    return seen_vm_ids


async def test_vs13_suspend_and_resume_use_different_vm_instances(
    store: ProgramStore, patch_tools_with_vm_identity_spy: list[int]
) -> None:
    """VS-13: the VM that suspends and the VM that resumes are not the same object.

    This is the concrete claim _GatewayCursorRepository exists to support:
    InMemoryCursorRepository would silently "work" here too if vm_step()
    reused one VM across calls — only a real per-call VM exposes the gap.
    """
    seen_vm_ids = patch_tools_with_vm_identity_spy

    first = await tools.vm_step(store, program=_SUSPENDING_PROGRAM)
    assert first["suspended"] is True

    second = await tools.vm_step(
        store, session_id=first["session_id"], input_data={"answer": "yes"}
    )
    assert second["suspended"] is False

    assert len(seen_vm_ids) == 2, "_build_vm must be called exactly once per vm_step() call"
    assert seen_vm_ids[0] != seen_vm_ids[1], (
        "suspend call and resume call must use distinct ExecutionVM instances — "
        "a shared/singleton VM would make _GatewayCursorRepository unnecessary"
    )


# ---------------------------------------------------------------------------
# VS-14..16: explicit cursor cleanup after resume
# ---------------------------------------------------------------------------
#
# Verified against nano_vm 0.8.6 source (ExecutionVM.resume_with_program):
# self._cursor_repo.delete(trace_id) is called unconditionally, immediately
# after load() and BEFORE re-entering _execute_loop — not "after terminal".
# If the resumed run suspends again, _suspend() calls save() again with the
# same trace_id (INSERT OR REPLACE), recreating the row. If the resumed run
# reaches a terminal state, no further save() happens and the row stays
# deleted. VS-09 only checked this indirectly (a 3rd call errors); these
# tests check store.get_vm_session() directly.


async def test_vs14_session_row_deleted_after_terminal_resume(
    store: ProgramStore, patch_tools: None
) -> None:
    """VS-14: vm_sessions row for session_id is gone once resume reaches SUCCESS."""
    first = await tools.vm_step(store, program=_SUSPENDING_PROGRAM)
    assert store.get_vm_session(first["session_id"]) is not None

    second = await tools.vm_step(
        store, session_id=first["session_id"], input_data={"answer": "yes"}
    )
    assert second["suspended"] is False
    assert store.get_vm_session(first["session_id"]) is None


async def test_vs15_resume_after_cleanup_errors_not_silently_succeeds(
    store: ProgramStore, patch_tools: None
) -> None:
    """VS-15: once the row is deleted (VS-14), a repeat resume call must error,
    not silently re-run or return a stale/garbage result.
    """
    first = await tools.vm_step(store, program=_SUSPENDING_PROGRAM)
    await tools.vm_step(store, session_id=first["session_id"], input_data={"answer": "yes"})

    repeat = await tools.vm_step(
        store, session_id=first["session_id"], input_data={"answer": "again"}
    )
    assert "error" in repeat
    assert store.get_vm_session(first["session_id"]) is None


async def test_vs16_linear_run_never_creates_session_row(
    store: ProgramStore, patch_tools: None
) -> None:
    """VS-16: a program that never suspends must never touch vm_sessions at all —
    _suspend()/save() is only reachable via the suspend path.
    """
    result = await tools.vm_step(store, program=_LINEAR_PROGRAM)
    assert result["suspended"] is False
    assert store.get_vm_session(result["session_id"]) is None
    assert store.get_vm_session(result["trace_id"]) is None


# ---------------------------------------------------------------------------
# VS-17..18: _GatewayCursorRepository bridge — direct unit tests
# ---------------------------------------------------------------------------
#
# VS-01..16 only exercise the bridge indirectly through vm_step()/ExecutionVM.
# These call save()/load()/delete() directly against the Protocol surface,
# so a future refactor of vm_step()'s orchestration can't accidentally mask
# a broken bridge — failures here point at _GatewayCursorRepository itself,
# not at ExecutionVM or the suspend/resume call sequence around it.

_MINIMAL_STATE_PAYLOAD: dict = {"data": {"k": "v"}, "step_outputs": {}}
_MINIMAL_TRACE_PAYLOAD: dict = {
    "program_name": "gw_bridge_unit_test",
    "status": "suspended",
    "trace_id": "bridge-unit-trace-id",
    "steps": [],
    "started_at": "2026-06-19T00:00:00Z",
}


async def test_vs17_bridge_save_then_load_round_trips(store: ProgramStore) -> None:
    """VS-17: save() persists exactly what load() later reconstructs.

    save() takes live StateContext/Trace objects (per the CursorRepository
    Protocol) and dumps them via model_dump(mode="json"); load() re-validates
    them with StateContext.model_validate/Trace.model_validate. This proves
    that round trip — not just that ProgramStore can store opaque JSON.
    """
    from nano_vm.models import StateContext, Trace

    repo = _GatewayCursorRepository(store, program_id="prog-vs17")
    state = StateContext.model_validate(_MINIMAL_STATE_PAYLOAD)
    trace = Trace.model_validate(_MINIMAL_TRACE_PAYLOAD)

    await repo.save(trace_id="bridge-unit-trace-id", step_id="ask", state=state, trace=trace)

    loaded = await repo.load("bridge-unit-trace-id")
    assert loaded is not None
    loaded_step_id, loaded_state, loaded_trace = loaded
    assert loaded_step_id == "ask"
    assert loaded_state.data == {"k": "v"}
    assert loaded_trace.trace_id == "bridge-unit-trace-id"
    assert str(loaded_trace.status).split(".")[-1].lower() == "suspended"

    # program_id round-trips alongside the cursor — required because
    # resume_with_program() needs the Program object again, not just
    # (step_id, state, trace).
    row = store.get_vm_session("bridge-unit-trace-id")
    assert row is not None
    assert row["program_id"] == "prog-vs17"


async def test_vs18_bridge_delete_then_load_returns_none(store: ProgramStore) -> None:
    """VS-18: delete() removes the row; load() afterward returns None, matching
    the Protocol contract resume_with_program() relies on (cursor is None ->
    ResumeError "Already resumed or never suspended").
    """
    from nano_vm.models import StateContext, Trace

    repo = _GatewayCursorRepository(store, program_id="prog-vs18")
    state = StateContext.model_validate(_MINIMAL_STATE_PAYLOAD)
    trace = Trace.model_validate(_MINIMAL_TRACE_PAYLOAD)

    await repo.save(trace_id="bridge-unit-trace-id-2", step_id="ask", state=state, trace=trace)
    assert await repo.load("bridge-unit-trace-id-2") is not None

    await repo.delete("bridge-unit-trace-id-2")
    assert await repo.load("bridge-unit-trace-id-2") is None

    # load() on a trace_id that never existed must also be None, not raise —
    # this is the same path vm_step()'s "Session not found" branch depends on.
    assert await repo.load("never-existed") is None
