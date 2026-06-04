"""
test_battery.py
---------------
Automated multi-step DC load test battery runner.

Runs a sequence of load steps from a battery config (defined in
plc/rig_config.json → test_batteries), recording averaged V/A/W
measurements at each step and evaluating voltage regulation pass/fail.

Per-step averaged results are saved to the battery_runs table.
Raw 1-Hz V/A/W data continues flowing into sensor_log via the existing
background recorder in app.py.

Thermal steady-state detection (for UL 1310 soak steps) reads TC2_DUT
via the thermal controller if connected; falls back to time-only if not.

Usage:
    battery = TestBattery(
        dc_load   = _dcload,
        thermal   = _thermal,    # optional — enables TC steady-state detection
        db        = conn,
        battery_id = "full_compliance_pec0063",
        metadata  = {"operator": "Jay", "dut": "PEC-0063 SN001"},
    )
    battery.start()
    status = battery.get_status()  # poll from Flask route
    battery.stop()                 # abort if needed
"""

from __future__ import annotations

import json
import os
import threading
import time
import statistics
from typing import Optional

_CFG_PATH = os.path.join(os.path.dirname(__file__), "plc", "rig_config.json")


def _load_batteries() -> dict:
    with open(_CFG_PATH) as f:
        return json.load(f).get("test_batteries", {})


# ── Tolerance check ───────────────────────────────────────────────────────────

def _voltage_ok(measured_v: float, nom_v: float, tol_pct: float) -> bool:
    """True when measured voltage is within ±tol_pct of nominal."""
    if nom_v == 0:
        return True
    return abs(measured_v - nom_v) / nom_v * 100 <= tol_pct


# ── Per-step result ───────────────────────────────────────────────────────────

def _blank_step_result(step: dict) -> dict:
    return {
        "step_id":       step["id"],
        "name":          step["name"],
        "mode":          step["mode"],
        "setpoint":      step["setpoint"],
        "pct_load":      step.get("pct_load", 0),
        "duration_s":    step.get("duration_s", 0),
        "state":         "pending",      # pending|running|done|aborted
        "readings":      [],             # [(elapsed_s, v, a, w)]
        "mean_v":        None,
        "mean_a":        None,
        "mean_w":        None,
        "voltage_ok":    None,
        "tc2_final_c":   None,
        "steady_state":  False,
        "elapsed_s":     0,
    }


# ── Battery runner ────────────────────────────────────────────────────────────

class TestBattery:
    """
    Run a configured multi-step test battery against a DC electronic load.

    Parameters
    ----------
    dc_load : SDL1020XDriver | None
        Connected DC load driver.  Pass None for simulation / dev.
    thermal : ThermalController | None
        Connected thermal controller.  Used for TC2_DUT steady-state checks
        on UL 1310 soak steps.  Pass None to skip thermal checks.
    db : sqlite3.Connection | None
        Live SQLite connection.  Results are written to battery_runs table.
    battery_id : str
        Key in rig_config.json → test_batteries.
    metadata : dict
        Freeform metadata stored with the run (operator, DUT serial, etc.).
    """

    def __init__(self, dc_load, thermal, db,
                 battery_id: str = "full_compliance_pec0063",
                 metadata: Optional[dict] = None):
        self._load    = dc_load
        self._thermal = thermal
        self._db      = db
        self._lock    = threading.Lock()
        self._stop    = threading.Event()
        self._thread: Optional[threading.Thread] = None

        batteries = _load_batteries()
        if battery_id not in batteries:
            raise ValueError(
                f"Unknown battery '{battery_id}'. "
                f"Available: {list(batteries)}"
            )
        self._cfg      = batteries[battery_id]
        self._meta     = metadata or {}
        self._battery_id = battery_id

        # Public status (updated by runner thread)
        self.state       = "idle"         # idle|running|done|aborted|error
        self.current_step_idx = 0
        self.step_results: list[dict] = [
            _blank_step_result(s) for s in self._cfg["steps"]
        ]
        self.error = ""
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.state == "running":
            raise RuntimeError("Battery already running.")
        self.state = "running"
        self.started_at = time.time()
        self._stop.clear()
        self.step_results = [_blank_step_result(s) for s in self._cfg["steps"]]
        self.current_step_idx = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)
        with self._lock:
            if self.state == "running":
                self.state = "aborted"
        self._safe_load_off()

    def get_status(self) -> dict:
        with self._lock:
            return {
                "state":          self.state,
                "battery_id":     self._battery_id,
                "battery_name":   self._cfg["name"],
                "current_step":   self.current_step_idx + 1,
                "total_steps":    len(self.step_results),
                "step_results":   list(self.step_results),
                "error":          self.error,
                "elapsed_s":      int(time.time() - self.started_at) if self.started_at else 0,
            }

    # ------------------------------------------------------------------
    # Internal runner
    # ------------------------------------------------------------------

    def _run(self) -> None:
        nom_v   = self._cfg.get("nom_voltage_v", 0)
        tol_pct = self._cfg.get("pass_voltage_tolerance_pct", 5.0)

        try:
            for idx, step in enumerate(self._cfg["steps"]):
                if self._stop.is_set():
                    break

                with self._lock:
                    self.current_step_idx = idx
                    self.step_results[idx]["state"] = "running"

                self._apply_step(step)
                readings, tc2_final, ss_reached = self._collect(step)

                # Compute averages from the second half of readings
                # (first half = stabilisation; second half = measurement window)
                half = max(1, len(readings) // 2)
                measure_readings = readings[half:] if len(readings) > 2 else readings
                mean_v = statistics.mean(r[1] for r in measure_readings)
                mean_a = statistics.mean(r[2] for r in measure_readings)
                mean_w = statistics.mean(r[3] for r in measure_readings)
                v_ok   = _voltage_ok(mean_v, nom_v, tol_pct) if step.get("setpoint", 0) > 0 else True

                with self._lock:
                    r = self.step_results[idx]
                    r["state"]        = "done"
                    r["readings"]     = readings
                    r["mean_v"]       = round(mean_v, 4)
                    r["mean_a"]       = round(mean_a, 4)
                    r["mean_w"]       = round(mean_w, 3)
                    r["voltage_ok"]   = v_ok
                    r["tc2_final_c"]  = tc2_final
                    r["steady_state"] = ss_reached
                    r["elapsed_s"]    = readings[-1][0] if readings else 0

            # All steps complete — save to DB
            self._safe_load_off()
            self._save_run()
            with self._lock:
                self.state = "done"
                self.finished_at = time.time()

        except Exception as exc:
            self._safe_load_off()
            with self._lock:
                self.state = "error"
                self.error = str(exc)

    def _apply_step(self, step: dict) -> None:
        if self._load is None:
            return
        mode     = step["mode"]
        setpoint = step["setpoint"]
        try:
            self._load.set_mode(mode)
            if mode == "CC":
                self._load.set_current(setpoint)
            elif mode == "CV":
                self._load.set_voltage(setpoint)
            elif mode == "CR":
                self._load.set_resistance(setpoint)
            elif mode == "CP":
                self._load.set_power(setpoint)
            if setpoint > 0:
                self._load.set_input(True)
            else:
                self._load.set_input(False)
        except Exception:
            pass

    def _collect(self, step: dict) -> tuple[list, Optional[float], bool]:
        """
        Poll measurements every 10 s for step duration.
        For steady-state steps, also check TC2_DUT window criterion.
        Returns (readings, tc2_final_c, steady_state_reached).
        """
        max_duration = step.get("duration_s", 600)
        is_ss_step   = step.get("steady_state", False)
        ss_tol       = step.get("steady_state_tolerance_c", 2.0)
        ss_window    = step.get("steady_state_window_s", 1800)
        poll         = 10   # seconds between measurements

        readings: list[tuple[int, float, float, float]] = []
        tc2_history: list[float] = []
        t_start = time.time()
        ss_reached = False

        while not self._stop.is_set():
            elapsed = int(time.time() - t_start)

            # Read SDL measurements
            v, a, w = self._read_load()
            readings.append((elapsed, v, a, w))

            # Read TC2_DUT for steady-state check
            tc2 = None
            if self._thermal and is_ss_step:
                try:
                    tc2 = self._thermal.read_temp("TC2_DUT")
                    if tc2 is not None:
                        tc2_history.append(tc2)
                except Exception:
                    pass

            # Check steady state
            if is_ss_step and len(tc2_history) >= max(1, ss_window // poll):
                window = tc2_history[-max(1, ss_window // poll):]
                if max(window) - min(window) < ss_tol:
                    ss_reached = True
                    break

            if elapsed >= max_duration:
                break

            self._stop.wait(timeout=poll)

        tc2_final = tc2_history[-1] if tc2_history else None
        return readings, tc2_final, ss_reached

    def _read_load(self) -> tuple[float, float, float]:
        if self._load is None:
            import random, time as t
            v = 20.0 + random.uniform(-0.1, 0.1)
            a = 1.5  + random.uniform(-0.05, 0.05)
            return v, a, round(v * a, 3)
        try:
            v = self._load.measure_voltage()
            a = self._load.measure_current()
            w = self._load.measure_power()
            return round(v, 4), round(a, 4), round(w, 3)
        except Exception:
            return 0.0, 0.0, 0.0

    def _safe_load_off(self) -> None:
        if self._load:
            try:
                self._load.set_input(False)
            except Exception:
                pass

    def _save_run(self) -> None:
        if self._db is None:
            return
        import json as _json, datetime
        steps_json = _json.dumps(
            [{k: v for k, v in r.items() if k != "readings"}
             for r in self.step_results]
        )
        readings_json = _json.dumps(
            {r["step_id"]: r["readings"] for r in self.step_results}
        )
        all_ok = all(
            r.get("voltage_ok", True)
            for r in self.step_results
            if r.get("setpoint", 0) > 0
        )
        try:
            self._db.execute(
                """INSERT INTO battery_runs
                   (battery_id, battery_name, started_at, finished_at,
                    operator, dut_id, overall_pass, steps_json, readings_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    self._battery_id,
                    self._cfg["name"],
                    datetime.datetime.fromtimestamp(self.started_at).isoformat(),
                    datetime.datetime.fromtimestamp(self.finished_at or time.time()).isoformat(),
                    self._meta.get("operator", ""),
                    self._meta.get("dut", ""),
                    1 if all_ok else 0,
                    steps_json,
                    readings_json,
                )
            )
            self._db.commit()
        except Exception as exc:
            print(f"[TestBattery] DB write error: {exc}")
