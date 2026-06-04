"""
database.py
-----------
SQLite schema and CRUD operations for the Juniper Test Station.

Schema:
  test_sessions  – one row per instrument test run (sequence-level)
  test_steps     – one row per step result within a test session
  sensor_log     – continuous 1-Hz sensor snapshots from the thermal rig
                   (always recording, independent of instrument tests)
  thermal_tests  – one row per PEC-0063 (or similar) thermal qualification run
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

SENSOR_LOG_DDL = """
CREATE TABLE IF NOT EXISTS sensor_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,   -- ISO-8601 timestamp, 1-Hz cadence
    instrument      TEXT,              -- active instrument at this moment
    hipot_running   INTEGER,           -- 1 if HiPot test in progress
    hipot_session   INTEGER,           -- foreign key to test_sessions.id (nullable)
    -- Thermocouple readings (°C, NULL if sensor not connected)
    tc1_c           REAL,              -- TC1: ambient / chamber air
    tc2_c           REAL,              -- TC2: DUT surface
    tc3_c           REAL,              -- TC3: heater element
    tc4_c           REAL,              -- TC4: exhaust / vent outlet
    -- Heater / PID state
    heater_duty     REAL,              -- 0–100 % SSR duty cycle
    setpoint_c      REAL,              -- PID target temperature
    vent_a_pct      REAL,              -- Vent A position 0–100 %
    vent_b_pct      REAL,              -- Vent B position 0–100 %
    control_active  INTEGER,           -- 1 if PID loop running
    thermal_fault   TEXT,              -- fault message if any
    -- PLC I/O snapshot
    plc_estop       INTEGER,           -- 1 = safe, 0 = E-stop tripped
    plc_door        INTEGER,           -- 1 = door closed
    plc_overtemp    INTEGER,           -- 1 = HW overtemp active
    -- DC Load live measurements (NULL if dcload not active)
    dcload_v        REAL,
    dcload_a        REAL,
    dcload_w        REAL,
    dcload_ohm      REAL,
    dcload_input_on INTEGER
);
CREATE INDEX IF NOT EXISTS sensor_log_ts ON sensor_log(ts);
CREATE INDEX IF NOT EXISTS sensor_log_session ON sensor_log(hipot_session);
"""

THERMAL_TESTS_DDL = """
CREATE TABLE IF NOT EXISTS thermal_tests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    housing_key     TEXT    NOT NULL,   -- e.g. DSK_Single, UDM_Triple
    standard        TEXT    NOT NULL,   -- UL_1310 | UL_962A
    surface_type    TEXT    NOT NULL,   -- metallic | nonmetallic
    dc_load_w       REAL,              -- applied load in watts
    tcase_c         REAL,              -- steady-state Tcase (°C)
    ambient_c       REAL,              -- ambient at time of steady state
    rise_c          REAL,              -- Tcase - ambient (°C)
    limit_c         REAL,              -- applicable UL limit
    margin_c        REAL,              -- positive = headroom, negative = over limit
    result          TEXT,              -- PASS | MARGINAL | FAIL
    note            TEXT               -- freeform note (e.g. test stopped early)
);
CREATE INDEX IF NOT EXISTS thermal_tests_housing ON thermal_tests(housing_key);
CREATE INDEX IF NOT EXISTS thermal_tests_result  ON thermal_tests(result);
"""



BATTERY_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS battery_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    battery_id      TEXT    NOT NULL,
    battery_name    TEXT,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    operator        TEXT,
    dut_id          TEXT,
    overall_pass    INTEGER,
    steps_json      TEXT,
    readings_json   TEXT
);
CREATE INDEX IF NOT EXISTS battery_runs_battery ON battery_runs(battery_id);
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


def ensure_sensor_log_table(db_path: str = DB_PATH) -> None:
    """Create the sensor_log and thermal_tests tables (called on startup)."""
    with get_connection(db_path) as conn:
        conn.executescript(SENSOR_LOG_DDL)
        conn.executescript(THERMAL_TESTS_DDL)
        conn.executescript(BATTERY_RUNS_DDL)


def get_battery_runs(limit: int = 100, db_path: str = DB_PATH) -> list[dict]:
    """Return most-recent test battery run results."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id,battery_id,battery_name,started_at,finished_at,operator,dut_id,overall_pass FROM battery_runs ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_battery_run(run_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    """Return a single battery run including full JSON data."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM battery_runs WHERE id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None


def get_thermal_tests(limit: int = 100, db_path: str = DB_PATH) -> list[dict]:
    """Return most-recent thermal qualification test results."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM thermal_tests ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def log_sensor_snapshot(snap: dict, db_path: str = DB_PATH) -> None:
    """Insert one sensor snapshot row from the continuous recorder."""
    cols = [
        "ts", "instrument", "hipot_running", "hipot_session",
        "tc1_c", "tc2_c", "tc3_c", "tc4_c",
        "heater_duty", "setpoint_c", "vent_a_pct", "vent_b_pct",
        "control_active", "thermal_fault",
        "plc_estop", "plc_door", "plc_overtemp",
        "dcload_v", "dcload_a", "dcload_w", "dcload_ohm", "dcload_input_on",
    ]
    vals = [snap.get(c) for c in cols]
    placeholders = ",".join("?" * len(cols))
    with get_connection(db_path) as conn:
        conn.execute(
            f"INSERT INTO sensor_log ({','.join(cols)}) VALUES ({placeholders})",
            vals
        )


def get_sensor_log(session_id: int = None, limit: int = 3600,
                   db_path: str = DB_PATH) -> list[dict]:
    """
    Return recent sensor log rows.
    If session_id given, returns rows spanning that test session.
    Otherwise returns the most recent `limit` rows.
    """
    with get_connection(db_path) as conn:
        if session_id:
            # Get session time window
            sess = conn.execute(
                "SELECT started_at, finished_at FROM test_sessions WHERE id=?",
                (session_id,)
            ).fetchone()
            if sess:
                q = """SELECT * FROM sensor_log
                       WHERE ts >= ? AND (ts <= ? OR ? IS NULL)
                       ORDER BY ts"""
                rows = conn.execute(q, (sess[0], sess[1], sess[1])).fetchall()
                return [dict(r) for r in rows]
        rows = conn.execute(
            "SELECT * FROM sensor_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


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
