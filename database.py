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
import json
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
    -- Shown in the UI and in reports as "Order Number". The column keeps
    -- its original name deliberately: packaged builds already in the field
    -- write to this database, and renaming it would break any older exe
    -- still running against the same ProgramData file. Label changed,
    -- storage unchanged.
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


PVD_DDL = """
CREATE TABLE IF NOT EXISTS pvd_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    profile_id      TEXT    NOT NULL,   -- key in pvd_profiles.json, e.g. apvd-74
    profile_name    TEXT,
    pvd_device      TEXT,               -- APVD-74 / APVD-7X / APVD-79
    pvd_serial      TEXT,               -- serial of the verification device itself
    mode            TEXT    NOT NULL,   -- verify | baseline
    operator        TEXT,
    device_model    TEXT,               -- V7X model from *IDN?
    device_serial   TEXT,
    firmware        TEXT,
    overall         TEXT,               -- PASS | FAIL | BASELINE | ABORTED | ERROR
    passed          INTEGER,            -- 1 pass, 0 fail, NULL no verdict (baseline/aborted)
    points_total    INTEGER,
    points_passed   INTEGER,
    points_skipped  INTEGER,
    notes           TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS pvd_runs_started ON pvd_runs(started_at);
CREATE INDEX IF NOT EXISTS pvd_runs_profile ON pvd_runs(profile_id);

CREATE TABLE IF NOT EXISTS pvd_points (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES pvd_runs(id),
    point_id        TEXT    NOT NULL,   -- e.g. acw_1000
    point_name      TEXT,
    phase           INTEGER,
    step_type       TEXT,               -- ACW | DCW | IR | CONT | GB
    unit            TEXT,               -- A | ohm
    measured        REAL,               -- value read back from STEPRSLT?
    nominal         REAL,               -- expected value from the profile (NULL when unbaselined)
    limit_low       REAL,
    limit_high      REAL,
    deviation_pct   REAL,               -- (measured - nominal) / nominal * 100
    status_flags    INTEGER,            -- V7X step status bitmask, 0 = clean
    flag_text       TEXT,               -- decoded status flags
    verdict         TEXT    NOT NULL,   -- PASS | FAIL | BASELINE | SKIPPED
    reason          TEXT,               -- why it failed or was skipped
    elapsed_s       REAL,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS pvd_points_run ON pvd_points(run_id);
"""

AUTH_DDL = """
CREATE TABLE IF NOT EXISTS app_settings (
    key             TEXT PRIMARY KEY,
    value           TEXT,
    updated_at      TEXT NOT NULL
);

-- Test sequences an admin has defined. Operators can run these but not edit
-- them, which is the whole point of the two-role split: the definition of a
-- qualification test is a controlled document, running it is not.
CREATE TABLE IF NOT EXISTS test_sequences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    description     TEXT,
    instrument      TEXT    NOT NULL DEFAULT 'hipot',
    steps_json      TEXT    NOT NULL,   -- the same step dicts /api/hipot/run accepts
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    created_by      TEXT,
    revision        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS test_sequences_active ON test_sequences(active);
"""

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    """Create tables if they don't exist, then apply additive migrations."""
    with get_connection(db_path) as conn:
        conn.executescript(DDL)
    ensure_result_detail_columns(db_path)


def _add_column_if_missing(conn, table: str, column: str, decl: str) -> bool:
    """
    Add a column only if it is absent. Returns True if it was added.

    Additive by design. Renaming or dropping a column would break any packaged
    build already in the field that writes to the same database file; adding one
    is invisible to an older exe, which simply leaves it NULL.
    """
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column in cols:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True


def ensure_result_detail_columns(db_path: str = DB_PATH) -> None:
    """
    Record what each test was configured to do, not only what it measured.

    Without these, an exported result shows "1500 V, 2.3 uA, PASS" with no way
    to tell whether it passed against a 5 mA limit or against no limit at all —
    which for a dielectric step set to "Breakdown Only" is a real difference.
    The instrument's global config is captured too, because ARC and FREQ change
    the pass/fail outcome and are not part of any sequence.
    """
    with get_connection(db_path) as conn:
        _add_column_if_missing(conn, "test_steps",    "config_json",        "TEXT")
        _add_column_if_missing(conn, "test_sessions", "sequence_name",      "TEXT")
        _add_column_if_missing(conn, "test_sessions", "sequence_revision",  "INTEGER")
        _add_column_if_missing(conn, "test_sessions", "instrument_config",  "TEXT")


def ensure_sensor_log_table(db_path: str = DB_PATH) -> None:
    """Create the sensor_log and thermal_tests tables (called on startup)."""
    with get_connection(db_path) as conn:
        conn.executescript(SENSOR_LOG_DDL)
        conn.executescript(THERMAL_TESTS_DDL)
        conn.executescript(BATTERY_RUNS_DDL)
        conn.executescript(PVD_DDL)
        conn.executescript(AUTH_DDL)


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
                   firmware: str = "", sequence_name: str = "",
                   sequence_revision: int = None, instrument_config: dict = None,
                   db_path: str = DB_PATH) -> int:
    """
    Insert a new test session row and return its id.

    serial_number is presented to users as "Order Number"; the column keeps its
    original name so older packaged builds sharing this database still work.
    """
    ensure_result_detail_columns(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO test_sessions
               (started_at, operator, part_number, serial_number, notes,
                device_model, device_serial, firmware,
                sequence_name, sequence_revision, instrument_config)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.datetime.now().isoformat(), operator, part_number,
             serial_number, notes, device_model, device_serial, firmware,
             sequence_name or None, sequence_revision,
             json.dumps(instrument_config) if instrument_config else None)
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
                     result: dict, config: dict = None,
                     db_path: str = DB_PATH) -> int:
    """
    Insert a step result row.

    result is the dict from V71Driver.step_result(); config is the step as it
    was programmed, so the record shows the limits the measurement was judged
    against rather than only the measurement.
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO test_steps
               (session_id, step_number, step_type, phase, elapsed_s,
                status_flags, passed, level, breakdown_a, measurement, arc_a,
                created_at, config_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                json.dumps(config) if config else None,
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


# ── PVD (Performance Verification Device) ─────────────────────────────────────

def ensure_pvd_tables(db_path: str = DB_PATH) -> None:
    """Create the PVD verification tables. Safe to call repeatedly."""
    with get_connection(db_path) as conn:
        conn.executescript(PVD_DDL)


def create_pvd_run(profile_id: str, profile_name: str = "", pvd_device: str = "",
                   pvd_serial: str = "", mode: str = "verify", operator: str = "",
                   device_model: str = "", device_serial: str = "", firmware: str = "",
                   notes: str = "", db_path: str = DB_PATH) -> int:
    """Open a PVD verification run and return its id."""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO pvd_runs
               (started_at, profile_id, profile_name, pvd_device, pvd_serial, mode,
                operator, device_model, device_serial, firmware, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.datetime.now().isoformat(), profile_id, profile_name,
             pvd_device, pvd_serial, mode, operator, device_model, device_serial,
             firmware, notes)
        )
        return cur.lastrowid


def save_pvd_point(run_id: int, point: dict, db_path: str = DB_PATH) -> int:
    """Insert one evaluated verification point."""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO pvd_points
               (run_id, point_id, point_name, phase, step_type, unit, measured,
                nominal, limit_low, limit_high, deviation_pct, status_flags,
                flag_text, verdict, reason, elapsed_s, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                point.get("point_id"),
                point.get("point_name"),
                point.get("phase"),
                point.get("step_type"),
                point.get("unit"),
                point.get("measured"),
                point.get("nominal"),
                point.get("limit_low"),
                point.get("limit_high"),
                point.get("deviation_pct"),
                point.get("status_flags", 0),
                point.get("flag_text"),
                point.get("verdict", "SKIPPED"),
                point.get("reason"),
                point.get("elapsed_s"),
                datetime.datetime.now().isoformat(),
            )
        )
        return cur.lastrowid


def finish_pvd_run(run_id: int, overall: str, points_total: int,
                   points_passed: int, points_skipped: int,
                   error: str = "", db_path: str = DB_PATH) -> None:
    """
    Close out a PVD run.

    passed is deliberately NULL for BASELINE and ABORTED runs — those produce
    measurements but no verdict, and recording them as a failure would make the
    verification history lie.
    """
    passed = 1 if overall == "PASS" else (0 if overall == "FAIL" else None)
    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE pvd_runs
               SET finished_at=?, overall=?, passed=?, points_total=?,
                   points_passed=?, points_skipped=?, error=?
               WHERE id=?""",
            (datetime.datetime.now().isoformat(), overall, passed, points_total,
             points_passed, points_skipped, error, run_id)
        )


def get_pvd_runs(limit: int = 100, db_path: str = DB_PATH) -> list[dict]:
    """Return most-recent PVD verification runs."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM pvd_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_pvd_run(run_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    """Return one PVD run with its evaluated points attached."""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM pvd_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        run = dict(row)
        pts = conn.execute(
            "SELECT * FROM pvd_points WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        run["points"] = [dict(p) for p in pts]
        return run


def get_last_passed_pvd(profile_id: str = "", db_path: str = DB_PATH) -> Optional[dict]:
    """
    Return the most recent PASSED verification run, optionally for one profile.

    Used to answer "is the instrument currently verified?" without scanning the
    whole history.
    """
    with get_connection(db_path) as conn:
        if profile_id:
            row = conn.execute(
                "SELECT * FROM pvd_runs WHERE passed=1 AND profile_id=? "
                "ORDER BY id DESC LIMIT 1", (profile_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM pvd_runs WHERE passed=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None


# ── App settings (key/value) ──────────────────────────────────────────────────

def ensure_auth_tables(db_path: str = DB_PATH) -> None:
    """Create app_settings and test_sequences. Safe to call repeatedly."""
    with get_connection(db_path) as conn:
        conn.executescript(AUTH_DDL)


def get_setting(key: str, default=None, db_path: str = DB_PATH):
    ensure_auth_tables(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str, db_path: str = DB_PATH) -> None:
    ensure_auth_tables(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                              updated_at=excluded.updated_at""",
            (key, value, datetime.datetime.now().isoformat())
        )


# ── Saved test sequences ──────────────────────────────────────────────────────

def list_sequences(instrument: str = "", active_only: bool = True,
                   db_path: str = DB_PATH) -> list[dict]:
    """
    Return saved sequences with their steps already decoded.

    A sequence whose steps_json is unparseable is returned with steps=[] and an
    error field rather than being dropped, so a corrupted row shows up in the UI
    as something to fix instead of quietly vanishing from the operator's list.
    """
    ensure_auth_tables(db_path)
    q = "SELECT * FROM test_sequences WHERE 1=1"
    params: list = []
    if active_only:
        q += " AND active=1"
    if instrument:
        q += " AND instrument=?"
        params.append(instrument)
    q += " ORDER BY name"
    with get_connection(db_path) as conn:
        rows = conn.execute(q, params).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        try:
            d["steps"] = json.loads(d.get("steps_json") or "[]")
        except (ValueError, TypeError) as exc:
            d["steps"] = []
            d["error"] = f"Stored steps are not readable: {exc}"
        out.append(d)
    return out


def get_sequence(seq_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    ensure_auth_tables(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM test_sequences WHERE id=?", (seq_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["steps"] = json.loads(d.get("steps_json") or "[]")
        except (ValueError, TypeError) as exc:
            d["steps"] = []
            d["error"] = f"Stored steps are not readable: {exc}"
        return d


def create_sequence(name: str, steps: list, description: str = "",
                    instrument: str = "hipot", created_by: str = "",
                    db_path: str = DB_PATH) -> int:
    ensure_auth_tables(db_path)
    now = datetime.datetime.now().isoformat()
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO test_sequences
               (name, description, instrument, steps_json, active,
                created_at, updated_at, created_by, revision)
               VALUES (?,?,?,?,1,?,?,?,1)""",
            (name, description, instrument, json.dumps(steps), now, now, created_by)
        )
        return cur.lastrowid


def update_sequence(seq_id: int, name: str = None, steps: list = None,
                    description: str = None, active: bool = None,
                    updated_by: str = "", db_path: str = DB_PATH) -> bool:
    """
    Update a sequence in place and bump its revision.

    The revision counter exists so a result record can be traced to the version
    of the sequence that produced it — editing a sequence after a batch has run
    should be visible, not silent.
    """
    ensure_auth_tables(db_path)
    sets, params = [], []
    if name is not None:
        sets.append("name=?"); params.append(name)
    if description is not None:
        sets.append("description=?"); params.append(description)
    if steps is not None:
        sets.append("steps_json=?"); params.append(json.dumps(steps))
    if active is not None:
        sets.append("active=?"); params.append(1 if active else 0)
    if not sets:
        return False
    sets.append("updated_at=?"); params.append(datetime.datetime.now().isoformat())
    sets.append("created_by=COALESCE(NULLIF(?,''), created_by)"); params.append(updated_by)
    sets.append("revision=revision+1")
    params.append(seq_id)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            f"UPDATE test_sequences SET {', '.join(sets)} WHERE id=?", params)
        return cur.rowcount > 0


def delete_sequence(seq_id: int, hard: bool = False, db_path: str = DB_PATH) -> bool:
    """
    Retire a sequence. Soft by default — deactivating keeps the definition
    around for anyone auditing an old result that referenced it.
    """
    ensure_auth_tables(db_path)
    with get_connection(db_path) as conn:
        if hard:
            cur = conn.execute("DELETE FROM test_sequences WHERE id=?", (seq_id,))
        else:
            cur = conn.execute(
                "UPDATE test_sequences SET active=0, updated_at=? WHERE id=?",
                (datetime.datetime.now().isoformat(), seq_id))
        return cur.rowcount > 0
