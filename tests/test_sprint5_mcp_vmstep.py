"""tests/test_sprint5_mcp_vmstep.py
VS-01..VS-12 — vm.step() sprint (sprint_5_mcp_vmstep)

Scope cut (DECISIONS.md 2026-06-18): no circuit_breaker, no
PROGRAM_IPN_HANDLER — covered separately by sprint_5_mcp_pending.
"""

from __future__ import annotations

import pytest

from nano_vm_mcp import tools
from nano_vm_mcp.handlers import build_chain
from nano_vm_mcp.store import ProgramStore

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
