"""Tests for ProgramStore (SQLite WAL)."""
import pytest
from nano_vm_mcp.store import ProgramStore


@pytest.fixture
def store(tmp_path):
    return ProgramStore(str(tmp_path / "test.db"))


def test_save_and_get_program(store):
    prog = {"steps": [{"id": "s1", "type": "tool", "tool": "noop"}]}
    store.save_program("p1", "my-prog", prog)
    result = store.get_program("p1")
    assert result == prog


def test_get_program_missing(store):
    assert store.get_program("nonexistent") is None


def test_list_programs_empty(store):
    assert store.list_programs() == []


def test_list_programs(store):
    store.save_program("p1", "first", {"steps": []})
    store.save_program("p2", "second", {"steps": []})
    items = store.list_programs()
    assert len(items) == 2
    ids = {i["id"] for i in items}
    assert ids == {"p1", "p2"}


def test_delete_program(store):
    store.save_program("p1", "to-delete", {"steps": []})
    assert store.delete_program("p1") is True
    assert store.get_program("p1") is None


def test_delete_program_missing(store):
    assert store.delete_program("ghost") is False


def test_save_and_get_trace(store):
    store.save_program("p1", "prog", {"steps": []})
    trace = {"status": "COMPLETED", "steps": [], "total_cost": 0.01}
    store.save_trace("t1", "p1", "COMPLETED", 0, 0.01, trace)
    result = store.get_trace("t1")
    assert result == trace


def test_get_trace_missing(store):
    assert store.get_trace("no-such-trace") is None


def test_delete_program_cascades_trace(store):
    """Deleting program must cascade to its traces (FK ON DELETE CASCADE)."""
    store.save_program("p1", "prog", {"steps": []})
    store.save_trace("t1", "p1", "COMPLETED", 0, 0.0, {"status": "COMPLETED"})
    store.delete_program("p1")
    assert store.get_trace("t1") is None
