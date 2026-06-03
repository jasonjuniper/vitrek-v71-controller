"""
pec0063_test.py
---------------
Thermal qualification test runner for PEC-0063 (65 W USB-C power supply).

Test procedure (per DSR 10/7/2025):
  1. Apply a 66 W constant-power load via the SDL1020X-E DC electronic load.
  2. Poll TC2_DUT (Tcase — surface of DUT enclosure) and TC1_AMBIENT every
     10 seconds until steady state is reached.
  3. Declare steady state when Tcase varies < 1 °C over any 5-minute window.
  4. Record final Tcase, ambient, and temperature rise (ΔT = Tcase - ambient).
  5. Evaluate against the selected UL standard:
       UL 962A  (household furnishings): Tcase ≤ 95 °C (nonmetallic surface)
       UL 1310  (class 2 power units):   ΔT    ≤ 50 °C (nonmetallic surface)
  6. Save result to the thermal_tests table.

All sensor readings are also captured in the continuous sensor_log table
(written by the recorder thread in app.py) so the full temperature curve is
preserved and can be plotted after the fact.

Usage:
    test = PEC0063Test(thermal_ctrl, dc_load, db_conn, housing_key="DSK_Single")
    test.start()
    # poll test.get_status() from a Flask route / background thread
    result = test.wait_for_result()   # blocks until done or timeout
    test.stop()
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

# ── Configuration ─────────────────────────────────────────────────────────────
_CFG_PATH = os.path.join(os.path.dirname(__file__), 'plc', 'rig_config.json')

def _load_cfg() -> dict:
    with open(_CFG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

# ── UL pass/fail logic ────────────────────────────────────────────────────────

UL_LIMITS = {
    "UL_962A": {
        "metallic":    {"mode": "absolute", "limit_c": 70},
        "nonmetallic": {"mode": "absolute", "limit_c": 95},
    },
    "UL_1310": {
        "metallic":    {"mode": "rise",     "limit_c": 30},
        "nonmetallic": {"mode": "rise",     "limit_c": 50},
    },
}

MARGIN_WARN_C = 5   # within 5 °C of limit → MARGINAL


def evaluate_ul(tcase_c: float, ambient_c: float,
                standard: str = "UL_1310",
                surface: str  = "nonmetallic") -> dict:
    """
    Return a result dict:
      {
        "standard":    "UL_1310",
        "surface":     "nonmetallic",
        "tcase_c":     85.2,
        "ambient_c":   23.1,
        "rise_c":      62.1,
        "limit_c":     50,
        "measured_c":  62.1,   # either absolute tcase or rise depending on mode
        "margin_c":   -12.1,   # positive = headroom, negative = over limit
        "result":      "FAIL", # "PASS" | "MARGINAL" | "FAIL"
      }
    """
    spec   = UL_LIMITS[standard][surface]
    rise   = tcase_c - ambient_c
    measured = tcase_c if spec["mode"] == "absolute" else rise
    limit    = spec["limit_c"]
    margin   = limit - measured

    if margin < 0:
        result = "FAIL"
    elif margin < MARGIN_WARN_C:
        result = "MARGINAL"
    else:
        result = "PASS"

    return {
        "standard":   standard,
        "surface":    surface,
        "tcase_c":    round(tcase_c,   2),
        "ambient_c":  round(ambient_c, 2),
        "rise_c":     round(rise,      2),
        "limit_c":    limit,
        "measured_c": round(measured,  2),
        "margin_c":   round(margin,    2),
        "result":     result,
    }


# ── Test state ────────────────────────────────────────────────────────────────

@dataclass
class TestStatus:
    state: str = "idle"           # idle | running | steady_state | done | aborted | error
    housing_key: str  = ""
    standard: str     = "UL_1310"
    surface: str      = "nonmetallic"
    elapsed_s: int    = 0
    tcase_c: float    = 0.0
    ambient_c: float  = 0.0
    rise_c: float     = 0.0
    load_w: float     = 0.0
    load_v: float     = 0.0
    load_a: float     = 0.0
    result: Optional[dict] = None
    error:  str       = ""
    readings: list    = field(default_factory=list)   # [(elapsed_s, tcase, ambient)]


class PEC0063Test:
    """
    Thread-safe thermal qualification test controller.

    Parameters
    ----------
    thermal_ctrl : ThermalController instance (from plc/thermal_controller.py)
        Used to read thermocouple temperatures.  Pass None to read mocked
        values (useful for unit tests or Windows dev runs).
    dc_load : SDL1020XDriver instance (from sdl1020x_driver.py)
        Used to apply and remove the 66 W load.  Pass None to mock.
    db : sqlite3.Connection
        Live database connection.  The test writes to thermal_tests; caller
        owns the connection and must not close it during the test.
    housing_key : str
        One of the keys in rig_config.json → pec0063_thermal_qualification
        → housings (e.g. "DSK_Single").
    standard : str
        "UL_1310" or "UL_962A".
    surface : str
        "metallic" or "nonmetallic".
    """

    def __init__(
        self,
        thermal_ctrl,
        dc_load,
        db,
        housing_key: str  = "DSK_Single",
        standard: str     = "UL_1310",
        surface: str      = "nonmetallic",
    ):
        self._tc    = thermal_ctrl
        self._load  = dc_load
        self._db    = db
        self._lock  = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        cfg = _load_cfg()
        self._pec_cfg  = cfg["pec0063_thermal_qualification"]
        self._ul_cfg   = cfg["ul_temperature_limits"]

        self.status = TestStatus(
            housing_key = housing_key,
            standard    = standard,
            surface     = surface,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the test in a background thread."""
        with self._lock:
            if self.status.state == "running":
                raise RuntimeError("Test already running.")
            self.status = TestStatus(
                housing_key = self.status.housing_key,
                standard    = self.status.standard,
                surface     = self.status.surface,
            )
            self.status.state = "running"
            self._stop_event.clear()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Abort a running test."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)
        with self._lock:
            if self.status.state == "running":
                self.status.state = "aborted"

    def get_status(self) -> dict:
        with self._lock:
            s = self.status
            d = {
                "state":       s.state,
                "housing":     s.housing_key,
                "standard":    s.standard,
                "surface":     s.surface,
                "elapsed_s":   s.elapsed_s,
                "tcase_c":     s.tcase_c,
                "ambient_c":   s.ambient_c,
                "rise_c":      s.rise_c,
                "load_w":      s.load_w,
                "load_v":      s.load_v,
                "load_a":      s.load_a,
                "result":      s.result,
                "error":       s.error,
            }
        return d

    # ------------------------------------------------------------------
    # Internal test loop
    # ------------------------------------------------------------------

    def _read_temps(self) -> tuple[float, float]:
        """Return (tcase_c, ambient_c). Uses mock values if no thermal_ctrl."""
        if self._tc is None:
            import random
            t = self.status.elapsed_s
            tcase   = 25.0 + min(t / 200, 1.0) * 63.0 + random.uniform(-0.3, 0.3)
            ambient = 24.0 + random.uniform(-0.2, 0.2)
            return tcase, ambient

        temps = self._tc.read_all_temps()
        tcase_ch   = self._pec_cfg["tcase_channel"]
        ambient_ch = self._pec_cfg["ambient_channel"]
        return temps.get(tcase_ch, 0.0), temps.get(ambient_ch, 0.0)

    def _read_load(self) -> tuple[float, float, float]:
        """Return (watts, volts, amps). Mocked if no dc_load driver."""
        if self._load is None:
            w = self._pec_cfg["dc_load"]["setpoint_w"]
            return w, 20.0, round(w / 20.0, 3)
        try:
            v = self._load.measure_voltage()
            a = self._load.measure_current()
            return round(v * a, 2), round(v, 3), round(a, 3)
        except Exception:
            return 0.0, 0.0, 0.0

    def _apply_load(self) -> None:
        if self._load is None:
            return
        cfg = self._pec_cfg["dc_load"]
        self._load.set_mode("CP")
        self._load.set_power(cfg["setpoint_w"])
        self._load.set_ovp(cfg["ovp_limit_v"])
        self._load.set_ocp(cfg["ocp_limit_a"])
        self._load.set_input(True)

    def _remove_load(self) -> None:
        if self._load is None:
            return
        try:
            self._load.set_input(False)
        except Exception:
            pass

    def _is_steady_state(self, window: list[float]) -> bool:
        """True when all readings in the window span < tolerance_c."""
        tol = self._pec_cfg["steady_state"]["tolerance_c"]
        win = self._pec_cfg["steady_state"]["window_s"]
        poll = self._pec_cfg["poll_interval_s"]
        needed = max(1, win // poll)
        if len(window) < needed:
            return False
        recent = window[-needed:]
        return (max(recent) - min(recent)) < tol

    def _save_result(self, result: dict, note: str = "") -> None:
        if self._db is None:
            return
        try:
            cur = self._db.cursor()
            cur.execute(
                """INSERT OR IGNORE INTO thermal_tests
                   (housing_key, standard, surface_type,
                    dc_load_w, tcase_c, ambient_c, rise_c,
                    limit_c, margin_c, result, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.status.housing_key,
                    result["standard"],
                    result["surface"],
                    self._pec_cfg["dc_load"]["setpoint_w"],
                    result["tcase_c"],
                    result["ambient_c"],
                    result["rise_c"],
                    result["limit_c"],
                    result["margin_c"],
                    result["result"],
                    note,
                ),
            )
            self._db.commit()
        except Exception as e:
            print(f"[PEC0063] DB write error: {e}")

    def _run(self) -> None:
        cfg     = self._pec_cfg
        max_s   = cfg["max_duration_s"]
        poll_s  = cfg["poll_interval_s"]
        t_start = time.time()
        tcase_window: list[float] = []

        try:
            self._apply_load()

            while not self._stop_event.is_set():
                elapsed = int(time.time() - t_start)

                tcase, ambient = self._read_temps()
                w, v, a        = self._read_load()
                rise = tcase - ambient

                tcase_window.append(tcase)

                with self._lock:
                    self.status.elapsed_s  = elapsed
                    self.status.tcase_c    = round(tcase, 1)
                    self.status.ambient_c  = round(ambient, 1)
                    self.status.rise_c     = round(rise, 1)
                    self.status.load_w     = w
                    self.status.load_v     = v
                    self.status.load_a     = a
                    self.status.readings.append((elapsed, round(tcase, 1), round(ambient, 1)))

                if self._is_steady_state(tcase_window):
                    with self._lock:
                        self.status.state = "steady_state"

                    result = evaluate_ul(
                        tcase, ambient,
                        standard=self.status.standard,
                        surface=self.status.surface,
                    )
                    self._save_result(result)

                    with self._lock:
                        self.status.result = result
                        self.status.state  = "done"

                    self._remove_load()
                    return

                if elapsed >= max_s:
                    # Safety cutoff — record whatever we have
                    result = evaluate_ul(
                        tcase, ambient,
                        standard=self.status.standard,
                        surface=self.status.surface,
                    )
                    self._save_result(result, note="Maximum test duration reached — may not be steady state")
                    with self._lock:
                        self.status.result = result
                        self.status.state  = "done"
                        self.status.error  = "Max duration reached without steady state"
                    self._remove_load()
                    return

                self._stop_event.wait(timeout=poll_s)

            # Stopped by caller
            self._remove_load()
            with self._lock:
                self.status.state = "aborted"

        except Exception as e:
            self._remove_load()
            with self._lock:
                self.status.state = "error"
                self.status.error = str(e)
