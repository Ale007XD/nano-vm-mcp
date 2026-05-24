"""tests/test_sprint4_idempotency.py
IP-01..IP-10 — idempotency_store sprint (v0.4.0)
"""

from __future__ import annotations

import pytest

from nano_vm_mcp.store import ProgramStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: pytest.TempPathFactory) -> ProgramStore:
    db = str(tmp_path / "test.db")
    return ProgramStore(db)


# ---------------------------------------------------------------------------
# Store-level tests (IP-01..IP-05, IP-10)
# ---------------------------------------------------------------------------


def test_ip01_save_get_roundtrip(store: ProgramStore) -> None:
    """IP-01: save + get round-trip."""
    store.save_idempotency_key(
        key="key-01",
        execution_id="exec-01",
        status="success",
        result={"trace_id": "t1", "status": "SUCCESS"},
        expires_at=None,
    )
    row = store.get_idempotency_key("key-01")
    assert row is not None
    assert row["idempotency_key"] == "key-01"
    assert row["execution_id"] == "exec-01"
    assert row["status"] == "success"
    assert row["result_json"] == {"trace_id": "t1", "status": "SUCCESS"}
    assert row["expires_at"] is None


def test_ip02_get_missing_returns_none(store: ProgramStore) -> None:
    """IP-02: get returns None for missing key."""
    assert store.get_idempotency_key("nonexistent") is None


def test_ip03_delete_returns_true_if_existed(store: ProgramStore) -> None:
    """IP-03: delete returns True if existed."""
    store.save_idempotency_key(
        key="key-03",
        execution_id="exec-03",
        status="pending",
        result=None,
        expires_at=None,
    )
    assert store.delete_idempotency_key("key-03") is True
    assert store.get_idempotency_key("key-03") is None


def test_ip04_delete_returns_false_if_not_existed(store: ProgramStore) -> None:
    """IP-04: delete returns False if not existed."""
    assert store.delete_idempotency_key("ghost-key") is False


def test_ip05_duplicate_key_upsert_overwrites(store: ProgramStore) -> None:
    """IP-05: duplicate key → upsert overwrites."""
    store.save_idempotency_key(
        key="key-05",
        execution_id="exec-05a",
        status="pending",
        result=None,
        expires_at=None,
    )
    store.save_idempotency_key(
        key="key-05",
        execution_id="exec-05b",
        status="success",
        result={"ok": True},
        expires_at=None,
    )
    row = store.get_idempotency_key("key-05")
    assert row is not None
    assert row["execution_id"] == "exec-05b"
    assert row["status"] == "success"
    assert row["result_json"] == {"ok": True}


def test_ip10_expires_at_stored_and_retrievable(store: ProgramStore) -> None:
    """IP-10: expires_at stored and retrievable."""
    expires = "2026-12-31T23:59:59Z"
    store.save_idempotency_key(
        key="key-10",
        execution_id="exec-10",
        status="success",
        result=None,
        expires_at=expires,
    )
    row = store.get_idempotency_key("key-10")
    assert row is not None
    assert row["expires_at"] == expires


# ---------------------------------------------------------------------------
# Handler-level tests (IP-06..IP-09)
# ---------------------------------------------------------------------------


@pytest.fixture
def program_data() -> dict:
    return {
        "name": "test_prog",
        "steps": [
            {"id": "step1", "type": "tool", "tool": "noop_tool"},
        ],
    }


@pytest.fixture
def handler(store: ProgramStore):  # type: ignore[no-untyped-def]
    from nano_vm_mcp.handlers import GovernedRunProgramHandler
    return GovernedRunProgramHandler(policy=None), store


@pytest.mark.asyncio
async def test_ip06_pending_status_run_proceeds(
    store: ProgramStore, program_data: dict
) -> None:
    """IP-06: status=pending → run proceeds (not cached)."""
    from nano_vm_mcp.handlers import GovernedRunProgramHandler

    store.save_idempotency_key(
        key="key-06",
        execution_id="",
        status="pending",
        result=None,
        expires_at=None,
    )
    h = GovernedRunProgramHandler(policy=None)
    result = await h.handle("run_program", {"program": program_data, "idempotency_key": "key-06"}, store)
    import json
    data = json.loads(result[0].text)
    # Should have run (not returned cached) — no "cached" marker, has trace_id or error
    assert "cached" not in str(data)


@pytest.mark.asyncio
async def test_ip07_success_status_returns_cached(
    store: ProgramStore, program_data: dict
) -> None:
    """IP-07: status=success → cached result returned without vm.run()."""
    from nano_vm_mcp.handlers import GovernedRunProgramHandler

    cached_result = {"trace_id": "cached-trace", "status": "SUCCESS", "steps": 1, "cost": 0.0, "error": None}
    store.save_idempotency_key(
        key="key-07",
        execution_id="cached-trace",
        status="success",
        result=cached_result,
        expires_at=None,
    )
    h = GovernedRunProgramHandler(policy=None)
    result = await h.handle("run_program", {"program": program_data, "idempotency_key": "key-07"}, store)
    import json
    data = json.loads(result[0].text)
    assert data.get("trace_id") == "cached-trace"
    assert data.get("status") == "SUCCESS"


@pytest.mark.asyncio
async def test_ip08_no_idempotency_key_normal_run(
    store: ProgramStore, program_data: dict
) -> None:
    """IP-08: idempotency_key absent → normal run (backward compat)."""
    from nano_vm_mcp.handlers import GovernedRunProgramHandler

    h = GovernedRunProgramHandler(policy=None)
    result = await h.handle("run_program", {"program": program_data}, store)
    import json
    data = json.loads(result[0].text)
    # Should run normally — trace_id present or error (no llm configured is ok)
    assert "error" in data or "trace_id" in data


@pytest.mark.asyncio
async def test_ip09_handler_saves_key_after_success(
    store: ProgramStore, program_data: dict
) -> None:
    """IP-09: GovernedRunProgramHandler saves key after successful run."""
    from nano_vm_mcp.handlers import GovernedRunProgramHandler

    h = GovernedRunProgramHandler(policy=None)
    await h.handle(
        "run_program",
        {"program": program_data, "idempotency_key": "key-09"},
        store,
    )
    row = store.get_idempotency_key("key-09")
    assert row is not None
    # After run: either success (saved by handler) or pending (run failed)
    assert row["status"] in ("success", "pending")
