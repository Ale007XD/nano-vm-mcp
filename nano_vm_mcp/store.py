"""nano_vm_mcp.store — SQLite WAL persistence for Programs, Traces, and GovernanceEnvelopes."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any


def _make_conn(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.row_factory = sqlite3.Row
    return con


def _init_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE TABLE IF NOT EXISTS programs (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL DEFAULT '',
            program_json TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE TABLE IF NOT EXISTS traces (
            id           TEXT PRIMARY KEY,
            program_id   TEXT NOT NULL,
            status       TEXT NOT NULL,
            steps_count  INTEGER NOT NULL DEFAULT 0,
            total_cost   REAL NOT NULL DEFAULT 0.0,
            trace_json   TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE TABLE IF NOT EXISTS state_contexts (
            trace_id     TEXT PRIMARY KEY,
            context_json TEXT NOT NULL,
            updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE TABLE IF NOT EXISTS governance_envelopes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id    TEXT    NOT NULL,
            step_id         INTEGER NOT NULL,
            policy_hash     TEXT    NOT NULL,
            snapshot_hash   TEXT    NOT NULL,
            payload_json    TEXT    NOT NULL,
            created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_gov_envelopes_execution_id
            ON governance_envelopes (execution_id);
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            idempotency_key TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            result_json     TEXT,
            created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            expires_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS execution_traces (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id    TEXT    NOT NULL,
            step_index      INTEGER NOT NULL,
            step_id         TEXT    NOT NULL,
            projected_json  TEXT    NOT NULL,
            canonical_hash  TEXT    NOT NULL,
            created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_exec_traces_execution_id
            ON execution_traces (execution_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_exec_traces_unique
            ON execution_traces (execution_id, step_index);
    """)
    con.commit()


class ProgramStore:
    """Thread-safe SQLite store with a single persistent connection per instance.

    One connection is created at construction time and reused for all operations.
    A threading.Lock serialises concurrent writes; reads run lock-free (WAL allows
    concurrent readers with a single writer).
    """

    def __init__(self, db_path: str) -> None:
        self._lock = threading.Lock()
        self._con = _make_conn(db_path)
        _init_schema(self._con)

    def close(self) -> None:
        """Close the underlying connection. Call on shutdown if needed."""
        self._con.close()

    # ------------------------------------------------------------------
    # Programs
    # ------------------------------------------------------------------

    def save_program(self, program_id: str, name: str, program: dict[str, Any]) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO programs (id, name, program_json) VALUES (?, ?, ?)",
                (program_id, name, json.dumps(program)),
            )
            self._con.commit()

    def get_program(self, program_id: str) -> dict[str, Any] | None:
        row = self._con.execute(
            "SELECT program_json FROM programs WHERE id = ?", (program_id,)
        ).fetchone()
        return json.loads(row["program_json"]) if row else None

    def list_programs(self) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT id, name, created_at FROM programs ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_program(self, program_id: str) -> bool:
        with self._lock:
            cur = self._con.execute("DELETE FROM programs WHERE id = ?", (program_id,))
            # Explicit cascade: delete associated traces (no FK after v0.3.1 schema change)
            self._con.execute("DELETE FROM traces WHERE program_id = ?", (program_id,))
            self._con.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Traces
    # ------------------------------------------------------------------

    def save_trace(
        self,
        trace_id: str,
        program_id: str,
        status: str,
        steps_count: int,
        total_cost: float,
        trace: dict[str, Any],
    ) -> None:
        with self._lock:
            self._con.execute(
                """INSERT OR REPLACE INTO traces
                   (id, program_id, status, steps_count, total_cost, trace_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (trace_id, program_id, status, steps_count, total_cost, json.dumps(trace)),
            )
            self._con.commit()

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        row = self._con.execute(
            "SELECT trace_json FROM traces WHERE id = ?", (trace_id,)
        ).fetchone()
        return json.loads(row["trace_json"]) if row else None

    # ------------------------------------------------------------------
    # StateContexts — TRACE projection persistence (v0.3.0)
    # ------------------------------------------------------------------

    def save_state_context(self, trace_id: str, context: dict[str, Any]) -> None:
        """Сохраняет (или перезаписывает) projection-контекст для trace_id."""
        with self._lock:
            self._con.execute(
                """INSERT OR REPLACE INTO state_contexts (trace_id, context_json, updated_at)
                   VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))""",
                (trace_id, json.dumps(context)),
            )
            self._con.commit()

    def load_state_context(self, trace_id: str) -> dict[str, Any] | None:
        """Возвращает projection-контекст по trace_id или None если не найден."""
        row = self._con.execute(
            "SELECT context_json FROM state_contexts WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        return json.loads(row["context_json"]) if row else None

    def delete_state_context(self, trace_id: str) -> bool:
        """Удаляет projection-контекст. Возвращает True если запись существовала."""
        with self._lock:
            cur = self._con.execute("DELETE FROM state_contexts WHERE trace_id = ?", (trace_id,))
            self._con.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # GovernanceEnvelopes — RFC v0.7.0 (Sprint4)
    # ------------------------------------------------------------------

    def save_envelope(
        self,
        execution_id: str,
        step_id: int,
        policy_hash: str,
        snapshot_hash: str,
        payload: dict[str, Any] | list[Any],
    ) -> int:
        """
        Сохраняет GovernanceEnvelope после каждого шага lifecycle.

        Args:
            execution_id:  trace_id / run identifier.
            step_id:       порядковый номер шага (0-based index из Trace).
            policy_hash:   PolicySnapshot.policy_hash на момент шага.
            snapshot_hash: Trace.canonical_snapshot_hash() после шага.
            payload:       TRACE-projected payload (dict или list).

        Returns:
            rowid вставленной записи.
        """
        with self._lock:
            cur = self._con.execute(
                """INSERT INTO governance_envelopes
                       (execution_id, step_id, policy_hash, snapshot_hash, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (execution_id, step_id, policy_hash, snapshot_hash, json.dumps(payload)),
            )
            self._con.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def get_envelopes(self, execution_id: str) -> list[dict[str, Any]]:
        """
        Возвращает все GovernanceEnvelope для execution_id, отсортированные по step_id.

        Каждый элемент соответствует полям GovernanceEnvelope из RFC:
          execution_id, step_id, policy_hash, canonical_snapshot_hash, payload, created_at.
        """
        rows = self._con.execute(
            """SELECT execution_id, step_id, policy_hash, snapshot_hash,
                      payload_json, created_at
               FROM governance_envelopes
               WHERE execution_id = ?
               ORDER BY step_id""",
            (execution_id,),
        ).fetchall()
        return [
            {
                "execution_id": r["execution_id"],
                "step_id": r["step_id"],
                "policy_hash": r["policy_hash"],
                "canonical_snapshot_hash": r["snapshot_hash"],
                "payload": json.loads(r["payload_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def delete_envelopes(self, execution_id: str) -> int:
        """
        Удаляет все envelope для execution_id.
        Используется при forensic cleanup или тестах.

        Returns:
            Количество удалённых записей.
        """
        with self._lock:
            cur = self._con.execute(
                "DELETE FROM governance_envelopes WHERE execution_id = ?", (execution_id,)
            )
            self._con.commit()
            return cur.rowcount

    # ------------------------------------------------------------------
    # ExecutionTraces — TRACE projection logging (v0.4.1)
    # ------------------------------------------------------------------

    def save_trace_step(
        self,
        execution_id: str,
        step_index: int,
        step_id: str,
        projected: dict[str, Any],
        canonical_hash: str,
    ) -> int:
        with self._lock:
            cur = self._con.execute(
                """INSERT OR IGNORE INTO execution_traces
                       (execution_id, step_index, step_id, projected_json, canonical_hash)
                   VALUES (?, ?, ?, ?, ?)""",
                (execution_id, step_index, step_id, json.dumps(projected), canonical_hash),
            )
            self._con.commit()
            return cur.lastrowid if cur.rowcount > 0 else 0  # type: ignore[return-value]

    def get_trace_steps(self, execution_id: str) -> list[dict[str, Any]]:
        rows = self._con.execute(
            """SELECT execution_id, step_index, step_id, projected_json,
                      canonical_hash, created_at
               FROM execution_traces
               WHERE execution_id = ?
               ORDER BY step_index""",
            (execution_id,),
        ).fetchall()
        return [
            {
                "execution_id": r["execution_id"],
                "step_index": r["step_index"],
                "step_id": r["step_id"],
                "projected": json.loads(r["projected_json"]),
                "canonical_hash": r["canonical_hash"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # IdempotencyKeys — exactly-once guarantee (v0.4.0)
    # ------------------------------------------------------------------

    def save_idempotency_key(
        self,
        key: str,
        execution_id: str,
        status: str,
        result: dict[str, Any] | None,
        expires_at: str | None,
    ) -> None:
        """
        INSERT OR REPLACE — upsert idempotency key record.

        Args:
            key:          Unique idempotency key from the caller.
            execution_id: Associated trace/execution identifier.
            status:       'pending' | 'success' | etc.
            result:       Execution result dict (stored as JSON) or None.
            expires_at:   Optional ISO-8601 expiration timestamp.
        """
        result_json: str | None = json.dumps(result) if result is not None else None
        with self._lock:
            self._con.execute(
                """INSERT OR REPLACE INTO idempotency_keys
                       (idempotency_key, execution_id, status, result_json, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, execution_id, status, result_json, expires_at),
            )
            self._con.commit()

    def get_idempotency_key(self, key: str) -> dict[str, Any] | None:
        """
        Возвращает запись idempotency key или None если не найдена.

        Returns:
            dict with fields: idempotency_key, execution_id, status,
            result_json (parsed as dict or None), created_at, expires_at.
        """
        row = self._con.execute(
            """SELECT idempotency_key, execution_id, status, result_json,
                      created_at, expires_at
               FROM idempotency_keys
               WHERE idempotency_key = ?""",
            (key,),
        ).fetchone()
        if row is None:
            return None
        result_raw: str | None = row["result_json"]
        return {
            "idempotency_key": row["idempotency_key"],
            "execution_id": row["execution_id"],
            "status": row["status"],
            "result_json": json.loads(result_raw) if result_raw is not None else None,
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }

    def delete_idempotency_key(self, key: str) -> bool:
        """
        Удаляет idempotency key. Возвращает True если запись существовала.
        """
        with self._lock:
            cur = self._con.execute(
                "DELETE FROM idempotency_keys WHERE idempotency_key = ?", (key,)
            )
            self._con.commit()
            return cur.rowcount > 0
