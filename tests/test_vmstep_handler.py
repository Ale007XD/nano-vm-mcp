"""Tests for handlers.VmStepHandler and build_chain(tools=...) wiring."""

import json

import pytest

from nano_vm_mcp.handlers import build_chain
from nano_vm_mcp.store import ProgramStore


@pytest.fixture
def store(tmp_path):
    return ProgramStore(str(tmp_path / "test.db"))


def _suspending_tool(**kwargs):
    return "PENDING"


def _greet_tool(**kwargs):
    webhook = kwargs.get("webhook") or {}
    name = webhook.get("name", "stranger") if isinstance(webhook, dict) else "stranger"
    return f"Hello, {name}!"


TOOLS = {"suspending_tool": _suspending_tool, "greet_tool": _greet_tool}

PROGRAM = {
    "name": "greet_flow",
    "steps": [
        {
            "id": "ask_name",
            "type": "tool",
            "tool": "suspending_tool",
            "args": {},
            "output_key": "np",
            "next_step": "greet",
        },
        {
            "id": "greet",
            "type": "tool",
            "tool": "greet_tool",
            "args": {"webhook": "$__webhook__"},
            "output_key": "g",
            "is_terminal": True,
        },
    ],
}


async def test_vm_step_routed_through_chain(store):
    chain = build_chain(tools=TOOLS)
    result = await chain.handle(
        "vm_step",
        {"session_id": "chat1", "input": {}, "program": PROGRAM},
        store,
    )
    payload = json.loads(result[0].text)
    assert payload["status"] == "SUSPENDED"
    assert payload["suspended"] is True


async def test_vm_step_resume_through_chain(store):
    chain = build_chain(tools=TOOLS)
    await chain.handle(
        "vm_step", {"session_id": "chat1", "input": {}, "program": PROGRAM}, store
    )
    result = await chain.handle(
        "vm_step", {"session_id": "chat1", "input": {"name": "Sasha"}}, store
    )
    payload = json.loads(result[0].text)
    assert payload["status"] == "SUCCESS"
    assert payload["output"] == "Hello, Sasha!"


async def test_vm_step_without_tools_registry_fails_cleanly(store):
    """build_chain() with no tools= still routes vm_step but the underlying
    program execution fails since no TOOL callables are registered."""
    chain = build_chain()  # no tools passed
    result = await chain.handle(
        "vm_step", {"session_id": "chat1", "input": {}, "program": PROGRAM}, store
    )
    payload = json.loads(result[0].text)
    # Either an explicit error, or a FAILED trace status -- must not silently
    # report SUCCESS/SUSPENDED with a real action having happened.
    assert payload.get("error") is not None or payload.get("status") == "FAILED"


async def test_other_tools_still_route_correctly_after_vm_step_insertion(store):
    """VmStepHandler insertion into the chain must not break existing handlers."""
    chain = build_chain(tools=TOOLS)
    result = await chain.handle("list_programs", {}, store)
    payload = json.loads(result[0].text)
    assert payload == []


async def test_unknown_tool_still_falls_through_to_unknown_handler(store):
    chain = build_chain(tools=TOOLS)
    result = await chain.handle("totally_unknown_tool", {}, store)
    payload = json.loads(result[0].text)
    assert "error" in payload
