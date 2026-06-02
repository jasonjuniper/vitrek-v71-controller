"""
database.py
-----------
SQLite schema and CRUD operations for V71 test results.

Schema:
  test_sessions  – one row per test run (sequence-level)
  test_steps     – one row per step result within a session
"""

import sqlite3
import datetime
import os
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hipot_results.db")

DDL = """
CREATE TABLE IF NOT EXISTS test_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    operator        TEXT,
    part_number     TEXT,
    serial_number   TEXT,
    notes           TEXT,
    overall_result  INTEGER,          -- RSLT? bitmask (0 = pass)
    passed          INTEGER,          -- 1 = pass, 0 = fail, NULL = incomplete
    device_model    TEXT,
    device_serial   TEXT,
    firmware        TEXT
);

CREATE TABLE IF NOT EXISTS test_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES test_sessions(id),
    step_number     INTEGER NOT NULL,
    step_type       TEXT,             -- ACW, DCW, IR, GB, CONT, PAUSE, HOLD
    phase           TEXT,             -- not_executed, terminated_during_dwell, etc.
    elapsed_s       REAL,
    status_flags    INTEGER,
    passed          INTEGER,          -- 1 = pass, 0 = fail
    level           REAL,             -- test voltage (V) or current (A)
    breakdown_a     REAL,             -- peak breakdown current
    measurement     REAL,             -- leakage current (A) or resistance (Ω)
    arc_a           REAL,             -- highest arc current
    created_at      TEXT    NOT NULL
);
"""


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    """Create tables if they don't exist."""
    with get_connection(db_path) as conn:
        conn.executescript(DDL)


def create_session(operator: str = "", part_number: str = "", serial_number: str = "",
                   notes: str = "", device_model: str = "", device_serial: str = "",
                   firmware: str = "", db_path: str = DB_PATH) -> int:
    """Insert a new test session row and return its id."""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO test_sessions
               (started_at, operator, part_number, serial_number, notes,
                device_model, device_serial, firmware)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.datetime.now().isoformat(), operator, part_number,
             serial_number, notes, device_model, device_serial, firmware)
        )
        return cur.lastrowid


def finish_session(session_id: int, overall_result: int,
                   db_path: str = DB_PATH) -> None:
    """Mark a session as finished with its pass/fail result."""
    passed = 1 if overall_result == 0 else 0
    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE test_sessions
               SET finished_at=?, overall_result=?, passed=?
               WHERE id=?""",
            (datetime.datetime.now().isoformat(), overall_result, passed, session_id)
        )


def save_step_result(session_id: int, step_number: int, step_type: str,
                     result: dict, db_path: str = DB_PATH) -> int:
    """Insert a step result row. result is the dict from V71Driver.step_result()."""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO test_steps
               (session_id, step_number, step_type, phase, elapsed_s,
                status_flags, passed, level, breakdown_a, measurement, arc_a, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                step_number,
                step_type,
                result.get("phase"),
                result.get("elapsed_s"),
                result.get("status_flags", 0),
                1 if result.get("passed") else 0,
                result.get("level"),
                result.get("breakdown_a"),
                result.get("measurement"),
                result.get("arc_a"),
                datetime.datetime.now().isoformat(),
            )
        )
        return cur.lastrowid


def get_sessions(limit: int = 100, db_path: str = DB_PATH) -> list[dict]:
    """Return most-recent sessions as a list of dicts."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM test_sessions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_session(session_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM test_sessions WHERE id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def get_steps(session_id: int, db_path: str = DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM test_steps WHERE session_id=? ORDER BY step_number",
            (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_stats(db_path: str = DB_PATH) -> dict:
    """Return aggregate pass/fail counts."""
    with get_connection(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM test_sessions WHERE passed IS NOT NULL").fetchone()[0]
        passed = conn.execute("SELECT COUNT(*) FROM test_sessions WHERE passed=1").fetchone()[0]
        return {"total": total, "passed": passed, "failed": total - passed}
