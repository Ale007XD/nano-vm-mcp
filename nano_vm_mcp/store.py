"""nano_vm_mcp.store — SQLite WAL persistence for Programs and Traces."""

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
            created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE
        );
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
