"""Tests for ProgramStore.vm_sessions and vm_cursors (sprint_5_mcp_vmstep)."""

import pytest

from nano_vm_mcp.store import ProgramStore


@pytest.fixture
def store(tmp_path):
    return ProgramStore(str(tmp_path / "test.db"))


# --- vm_sessions ---------------------------------------------------------


def test_save_and_get_vm_session(store):
    store.save_vm_session("sess1", "trace1", "prog1")
    result = store.get_vm_session("sess1")
    assert result == {"session_id": "sess1", "trace_id": "trace1", "program_id": "prog1"}


def test_get_vm_session_missing(store):
    assert store.get_vm_session("ghost") is None


def test_save_vm_session_upserts(store):
    """Resuming a session overwrites trace_id/program_id, not append."""
    store.save_vm_session("sess1", "trace1", "prog1")
    store.save_vm_session("sess1", "trace2", "prog1")
    result = store.get_vm_session("sess1")
    assert result["trace_id"] == "trace2"


def test_delete_vm_session(store):
    store.save_vm_session("sess1", "trace1", "prog1")
    assert store.delete_vm_session("sess1") is True
    assert store.get_vm_session("sess1") is None


def test_delete_vm_session_missing(store):
    assert store.delete_vm_session("ghost") is False


# --- vm_cursors ------------------------------------------------------------


def test_save_and_load_vm_cursor(store):
    store.save_vm_cursor("trace1", "step_a", '{"data":{}}', '{"trace_id":"trace1"}')
    result = store.load_vm_cursor("trace1")
    assert result == {
        "step_id": "step_a",
        "state_json": '{"data":{}}',
        "trace_json": '{"trace_id":"trace1"}',
    }


def test_load_vm_cursor_missing(store):
    assert store.load_vm_cursor("ghost") is None


def test_save_vm_cursor_overwrites(store):
    store.save_vm_cursor("trace1", "step_a", '{"v":1}', '{"v":1}')
    store.save_vm_cursor("trace1", "step_b", '{"v":2}', '{"v":2}')
    result = store.load_vm_cursor("trace1")
    assert result["step_id"] == "step_b"


def test_delete_vm_cursor(store):
    store.save_vm_cursor("trace1", "step_a", "{}", "{}")
    assert store.delete_vm_cursor("trace1") is True
    assert store.load_vm_cursor("trace1") is None


def test_delete_vm_cursor_missing(store):
    assert store.delete_vm_cursor("ghost") is False
