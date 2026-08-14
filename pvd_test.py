"""
pvd_test.py
-----------
Performance Verification Device (PVD) runner for the Vitrek V7X series.

WHY THIS EXISTS IN SOFTWARE AT ALL
----------------------------------
The V7X front panel has UTILITY MENU -> AUTO PVD, which drives Vitrek's own
APVD accessory through a guided verification. That routine is front-panel only:
the remote command set in Section 6 of the V7X Series Operating Manual has no
command to start it. RUN starts the *active programmed sequence* and nothing
else.

So rather than pretend to drive the built-in routine, this module reproduces
the verification as an ordinary programmed sequence against the APVD's known
loads. That turns out to be the better artefact for a production floor:

  * the built-in routine gives the operator a PASS/FAIL on a screen,
  * this gives you the actual measured value for every point, stored, trendable
    and exportable to the Excel workbook the assembly team already uses.

The manual explicitly sanctions this ("the user may wish to not use this
built-in sequence but use their own sequence", Section 7).

HOW PASS/FAIL IS DECIDED
------------------------
Two independent gates, and a point must clear both:

  1. Instrument integrity. STEPRSLT? returns a status-flag bitmask. Anything
     non-zero (breakdown, arc, interlock, abort, over-voltage...) fails the
     point outright, whatever the number says.
  2. Measurement window. The measured value must land inside
     nominal +/- tolerance. The limits programmed into the V7X step itself are
     deliberately WIDE, so the instrument runs the step to completion instead of
     tripping out and denying us a reading. The real judgement happens here.

If a point has no nominal (the shipped state — Vitrek does not publish the APVD
internal load values), the point is recorded with verdict BASELINE and the run
as a whole returns BASELINE, not PASS. An unbaselined profile must never be
able to report a passing verification.

MODEL CAPABILITY GATING
-----------------------
A V71 has ACW, DCW and CONT — no IR, no GB. An APVD-74 carries loads for all of
them. Pushing an unsupported step at the instrument earns ERR 2 and aborts the
whole sequence build, so points whose mode the connected model cannot perform
are skipped with a recorded reason rather than attempted.

PHASES
------
The APVD needs its leads moved partway through (hipot load, then low-resistance
load, then ground bond). Each phase is programmed and run as its own sequence,
with the operator acknowledging the connection change in between. Separate
sequences rather than one sequence with HOLD steps: it keeps the instrument
de-energised and idle while hands are on the leads, which is the whole point.

Usage:
    pvd = PVDVerification(
        driver     = _hipot,
        profile_id = "apvd-74",
        mode       = "verify",              # or "baseline"
        metadata   = {"operator": "Jay", "pvd_serial": "APVD-74 SN123"},
    )
    pvd.start()
    status = pvd.get_status()      # poll from Flask
    pvd.acknowledge_phase()        # operator confirmed the leads are connected
    pvd.stop()                     # abort
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from typing import Optional

import database as db
from paths import data_dir, bundled_dir


def _resolve_profiles_path() -> str:
    """pvd_profiles.json is mutable — baseline promotion rewrites it — so it lives
    in the writable data dir, not next to __file__ (which is the temp _MEIPASS dir
    under a onefile exe). On first run when frozen, seed it from the read-only copy
    bundled in the exe. In dev, data_dir() == bundled_dir() == the repo, so this is
    just the repo file with no copy and no behaviour change."""
    dst = os.path.join(data_dir(), "pvd_profiles.json")
    if not os.path.exists(dst):
        src = os.path.join(bundled_dir(), "pvd_profiles.json")
        try:
            if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
                shutil.copyfile(src, dst)
        except OSError:
            pass
    return dst


_PROFILES_PATH = _resolve_profiles_path()

# Verdicts
PASS = "PASS"
FAIL = "FAIL"
BASELINE = "BASELINE"
SKIPPED = "SKIPPED"
ABORTED = "ABORTED"
ERROR = "ERROR"


# ── Profile loading ───────────────────────────────────────────────────────────

def load_profiles(path: str = _PROFILES_PATH) -> dict:
    """Load pvd_profiles.json. Raises if it is missing or malformed."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_profiles(path: str = _PROFILES_PATH) -> list[dict]:
    """Summarise the available profiles for the UI."""
    data = load_profiles(path)
    out = []
    for pid, prof in data.get("profiles", {}).items():
        pts = prof.get("points", [])
        out.append({
            "id": pid,
            "name": prof.get("name", pid),
            "device": prof.get("device", ""),
            "description": prof.get("description", ""),
            "points": len(pts),
            "baselined": all(p.get("nominal") is not None for p in pts) if pts else False,
            "recommended_interval_hours": prof.get("recommended_interval_hours"),
        })
    return sorted(out, key=lambda p: p["id"])


def get_profile(profile_id: str, path: str = _PROFILES_PATH) -> dict:
    data = load_profiles(path)
    profs = data.get("profiles", {})
    if profile_id not in profs:
        raise ValueError(f"Unknown PVD profile '{profile_id}'. Available: {list(profs)}")
    return profs[profile_id]


# ── Tolerance evaluation ──────────────────────────────────────────────────────

def tolerance_window(point: dict, default_tol_pct: float) -> tuple:
    """
    Return (low, high) for a point, or (None, None) when it has no nominal.

    tol_abs wins over tol_pct when both are present — an absolute window is the
    only sane choice for a quantity that can legitimately sit near zero, where a
    percentage window would collapse to nothing.
    """
    nominal = point.get("nominal")
    if nominal is None:
        return (None, None)
    tol_abs = point.get("tol_abs")
    if tol_abs is not None:
        return (nominal - abs(tol_abs), nominal + abs(tol_abs))
    tol_pct = point.get("tol_pct")
    if tol_pct is None:
        tol_pct = default_tol_pct
    margin = abs(nominal) * (float(tol_pct) / 100.0)
    return (nominal - margin, nominal + margin)


def evaluate_point(point: dict, measured: Optional[float], status_flags: int,
                   flag_text: str, default_tol_pct: float) -> tuple:
    """
    Decide a point's verdict. Returns (verdict, reason, low, high, deviation_pct).

    Order matters. Instrument faults are checked before the measurement window,
    because a step that arced or tripped the interlock can still hand back a
    plausible-looking number, and treating that as a passing measurement would
    be the worst failure mode this module has.
    """
    nominal = point.get("nominal")
    low, high = tolerance_window(point, default_tol_pct)

    deviation = None
    if measured is not None and nominal not in (None, 0):
        deviation = (measured - nominal) / nominal * 100.0

    if status_flags:
        return (FAIL, f"Instrument reported: {flag_text or status_flags}",
                low, high, deviation)

    if measured is None:
        return (FAIL, "No measurement returned by the instrument", low, high, deviation)

    if nominal is None:
        return (BASELINE, "No nominal recorded yet — measurement captured only",
                low, high, deviation)

    if measured < low:
        return (FAIL, f"Below window ({measured:.6g} < {low:.6g})", low, high, deviation)
    if measured > high:
        return (FAIL, f"Above window ({measured:.6g} > {high:.6g})", low, high, deviation)

    return (PASS, "", low, high, deviation)


# ── Runner ────────────────────────────────────────────────────────────────────

class PVDVerification:
    """
    Run a PVD verification profile against a connected V7X.

    Parameters
    ----------
    driver : V71Driver | None
        Connected instrument driver. Pass None to run in simulation, which
        exercises the whole flow (phases, acknowledgements, evaluation, storage)
        with synthetic measurements and no hardware.
    profile_id : str
        Key in pvd_profiles.json.
    mode : str
        "verify" issues PASS/FAIL against the profile nominals.
        "baseline" records measurements and deliberately withholds a verdict.
    metadata : dict
        operator, pvd_serial, notes.
    db_path : str
        SQLite path; defaults to the project database.
    """

    def __init__(self, driver, profile_id: str = "apvd-74", mode: str = "verify",
                 metadata: Optional[dict] = None, db_path: str = db.DB_PATH,
                 profiles_path: str = _PROFILES_PATH):
        if mode not in ("verify", "baseline"):
            raise ValueError("mode must be 'verify' or 'baseline'")

        self._drv = driver
        self._profiles_path = profiles_path
        self._profile = get_profile(profile_id, profiles_path)
        self._profile_id = profile_id
        self._mode = mode
        self._meta = metadata or {}
        self._db_path = db_path

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ack = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._default_tol = float(self._profile.get("default_tol_pct", 10.0))
        self._caps: tuple = ()

        # Public state, read by get_status()
        self.state = "idle"          # idle|awaiting_connection|running|done|aborted|error
        self.run_id: Optional[int] = None
        self.overall: Optional[str] = None
        self.error = ""
        self.current_phase: Optional[dict] = None
        self.results: list[dict] = []
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.state in ("running", "awaiting_connection"):
            raise RuntimeError("A PVD verification is already in progress.")
        self.state = "running"
        self.started_at = time.time()
        self.finished_at = None
        self.overall = None
        self.error = ""
        self.results = []
        self._stop.clear()
        self._ack.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def acknowledge_phase(self) -> None:
        """Operator has made the connections shown for the current phase."""
        self._ack.set()

    def stop(self) -> None:
        """Abort the verification and make the instrument safe."""
        self._stop.set()
        self._ack.set()          # unblock a thread parked on a phase prompt
        self._safe_abort()
        if self._thread:
            self._thread.join(timeout=20)

    def get_status(self) -> dict:
        with self._lock:
            return {
                "state": self.state,
                "mode": self._mode,
                "profile_id": self._profile_id,
                "profile_name": self._profile.get("name", self._profile_id),
                "pvd_device": self._profile.get("device", ""),
                "run_id": self.run_id,
                "overall": self.overall,
                "error": self.error,
                "phase": self.current_phase,
                "results": list(self.results),
                "capabilities": list(self._caps),
                "elapsed_s": int(time.time() - self.started_at) if self.started_at else 0,
            }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _idn(self) -> dict:
        if self._drv is None:
            return {"model": "V71 (simulated)", "serial": "SIM", "firmware": "SIM"}
        try:
            return self._drv.identify()
        except Exception:
            return {"model": "", "serial": "", "firmware": ""}

    def _capabilities(self, model: str) -> tuple:
        if self._drv is None:
            # Simulation assumes a V71 — the instrument this station actually has.
            return ("ACW", "DCW", "CONT")
        try:
            from v71_driver import capabilities_for_model
            return capabilities_for_model(model)
        except Exception:
            return ("ACW", "CONT")

    def _points_for_phase(self, phase_id: int) -> list[dict]:
        return [p for p in self._profile.get("points", [])
                if int(p.get("phase", 1)) == int(phase_id)]

    def _phase_is_runnable(self, phase: dict) -> bool:
        """A phase with no performable points is skipped entirely, prompt and all."""
        pts = self._points_for_phase(phase.get("id"))
        return any(self._point_supported(p) for p in pts)

    def _point_supported(self, point: dict) -> bool:
        required = point.get("requires") or [point.get("type")]
        return all(r in self._caps for r in required)

    def _run(self) -> None:
        try:
            idn = self._idn()
            model = idn.get("model", "")
            self._caps = self._capabilities(model)

            self.run_id = db.create_pvd_run(
                profile_id=self._profile_id,
                profile_name=self._profile.get("name", ""),
                pvd_device=self._profile.get("device", ""),
                pvd_serial=self._meta.get("pvd_serial", ""),
                mode=self._mode,
                operator=self._meta.get("operator", ""),
                device_model=model,
                device_serial=idn.get("serial", ""),
                firmware=idn.get("firmware", ""),
                notes=self._meta.get("notes", ""),
                db_path=self._db_path,
            )

            # Record skipped points up front so the report shows the full profile
            # and makes it obvious *why* a point was not measured.
            for point in self._profile.get("points", []):
                if not self._point_supported(point):
                    self._record(point, None, 0, "", SKIPPED,
                                 f"{point.get('type')} not supported by {model or 'this model'}")

            for phase in self._profile.get("phases", []):
                if self._stop.is_set():
                    break
                if not self._phase_is_runnable(phase):
                    continue
                if not self._await_phase(phase):
                    break
                self._run_phase(phase)

            self._finish()

        except Exception as exc:
            self._safe_abort()
            with self._lock:
                self.state = "error"
                self.error = str(exc)
                self.overall = ERROR
                self.finished_at = time.time()
            self._close_run(ERROR, str(exc))

    def _await_phase(self, phase: dict) -> bool:
        """Show the connection prompt and block until acknowledged. False if aborted."""
        self._ack.clear()
        with self._lock:
            self.state = "awaiting_connection"
            self.current_phase = {
                "id": phase.get("id"),
                "name": phase.get("name", ""),
                "instructions": phase.get("instructions", []),
                "danger": phase.get("danger", ""),
            }
        while not self._ack.wait(timeout=0.25):
            if self._stop.is_set():
                return False
        if self._stop.is_set():
            return False
        with self._lock:
            self.state = "running"
        return True

    def _run_phase(self, phase: dict) -> None:
        points = [p for p in self._points_for_phase(phase.get("id"))
                  if self._point_supported(p)]
        if not points:
            return

        if self._drv is None:
            self._simulate_phase(points)
            return

        self._drv.new_sequence()
        for point in points:
            self._program_point(point)

        self._drv.run()

        # Poll to completion. The timeout is generous but bounded — a driver that
        # stops answering must not park this thread forever.
        budget = sum(float(p.get("params", {}).get("dwell", 5)) +
                     float(p.get("params", {}).get("ramp", 0)) for p in points) + 60
        deadline = time.time() + budget
        time.sleep(0.5)
        while time.time() < deadline:
            if self._stop.is_set():
                self._safe_abort()
                return
            try:
                if not self._drv.is_running():
                    break
            except Exception:
                break
            time.sleep(0.25)

        for idx, point in enumerate(points, start=1):
            try:
                res = self._drv.step_result(idx)
            except Exception as exc:
                self._record(point, None, 0, "", FAIL, f"Could not read result: {exc}")
                continue
            flags = int(res.get("status_flags") or 0)
            try:
                flag_text = ", ".join(self._drv.decode_status_flags(flags)) if flags else ""
            except Exception:
                flag_text = str(flags) if flags else ""
            self._evaluate_and_record(point, res.get("measurement"), flags, flag_text,
                                      res.get("elapsed_s"))

    def _program_point(self, point: dict) -> None:
        """
        Push one verification point onto the active sequence.

        The limits handed to the instrument are intentionally permissive. We want
        the step to complete and report a number; the tolerance judgement is ours,
        not the instrument's.
        """
        t = (point.get("type") or "").upper()
        prm = point.get("params", {})
        if t == "ACW":
            self._drv.add_acw_step(
                float(prm["voltage"]), float(prm.get("ramp", 1.0)),
                float(prm.get("dwell", 3.0)), float(prm.get("max_leakage", 0.005)))
        elif t == "DCW":
            self._drv.add_dcw_step(
                float(prm["voltage"]), float(prm.get("ramp", 1.0)),
                float(prm.get("dwell", 3.0)), float(prm.get("max_leakage", 0.005)))
        elif t == "IR":
            self._drv.add_ir_step(
                float(prm["voltage"]), float(prm.get("dwell", 5.0)),
                float(prm.get("min_resistance", 1000.0)), None,
                float(prm.get("precheck_delay", 0.0)))
        elif t == "GB":
            self._drv.add_gb_step(
                float(prm["current"]), float(prm.get("dwell", 3.0)),
                float(prm.get("max_ohm", 0.5)))
        elif t == "CONT":
            self._drv.add_cont_step(
                float(prm.get("dwell", 3.0)),
                float(prm["min_ohm"]) if prm.get("min_ohm") is not None else None,
                float(prm["max_ohm"]) if prm.get("max_ohm") is not None else None)
        else:
            raise ValueError(f"Unsupported PVD point type: {t}")

    def _simulate_phase(self, points: list[dict]) -> None:
        """
        Dev/CI path. Produces a value near the nominal when one exists, and a
        type-plausible value when it does not, so the UI and storage can be
        exercised end to end without a V71 on the bench.
        """
        import random
        for point in points:
            if self._stop.is_set():
                return
            time.sleep(0.2)
            nominal = point.get("nominal")
            if nominal is not None:
                measured = nominal * (1 + random.uniform(-0.03, 0.03))
            elif point.get("unit") == "A":
                measured = random.uniform(0.9e-3, 1.1e-3)
            else:
                measured = random.uniform(0.9, 1.1)
            self._evaluate_and_record(point, measured, 0, "", 3.0)

    def _evaluate_and_record(self, point, measured, flags, flag_text, elapsed) -> None:
        if self._mode == "baseline":
            verdict, reason = BASELINE, "Baseline run — no verdict issued"
            low, high = tolerance_window(point, self._default_tol)
            deviation = None
            if flags:
                verdict, reason = FAIL, f"Instrument reported: {flag_text or flags}"
        else:
            verdict, reason, low, high, deviation = evaluate_point(
                point, measured, flags, flag_text, self._default_tol)
        self._record(point, measured, flags, flag_text, verdict, reason,
                     low, high, deviation, elapsed)

    def _record(self, point, measured, flags, flag_text, verdict, reason,
                low=None, high=None, deviation=None, elapsed=None) -> None:
        row = {
            "point_id": point.get("id"),
            "point_name": point.get("name"),
            "phase": point.get("phase"),
            "step_type": point.get("type"),
            "unit": point.get("unit"),
            "label": point.get("label"),
            "measured": measured,
            "nominal": point.get("nominal"),
            "limit_low": low,
            "limit_high": high,
            "deviation_pct": round(deviation, 3) if deviation is not None else None,
            "status_flags": flags,
            "flag_text": flag_text,
            "verdict": verdict,
            "reason": reason,
            "elapsed_s": elapsed,
        }
        with self._lock:
            self.results.append(row)
        if self.run_id:
            try:
                db.save_pvd_point(self.run_id, row, db_path=self._db_path)
            except Exception as exc:
                print(f"[PVD] DB write error: {exc}")

    def _finish(self) -> None:
        measured = [r for r in self.results if r["verdict"] != SKIPPED]
        skipped = len(self.results) - len(measured)
        passed = sum(1 for r in measured if r["verdict"] == PASS)

        if self._stop.is_set():
            overall = ABORTED
            state = "aborted"
        elif self._mode == "baseline":
            overall = BASELINE
            state = "done"
        elif not measured:
            overall = ERROR
            state = "error"
        elif any(r["verdict"] == FAIL for r in measured):
            overall = FAIL
            state = "done"
        elif any(r["verdict"] == BASELINE for r in measured):
            # At least one point has no nominal, so this cannot claim to be a pass.
            overall = BASELINE
            state = "done"
        else:
            overall = PASS
            state = "done"

        with self._lock:
            self.state = state
            self.overall = overall
            self.current_phase = None
            self.finished_at = time.time()

        self._close_run(overall, "", len(self.results), passed, skipped)

    def _close_run(self, overall: str, error: str = "", total: int = None,
                   passed: int = None, skipped: int = None) -> None:
        if not self.run_id:
            return
        if total is None:
            total = len(self.results)
            passed = sum(1 for r in self.results if r["verdict"] == PASS)
            skipped = sum(1 for r in self.results if r["verdict"] == SKIPPED)
        try:
            db.finish_pvd_run(self.run_id, overall, total, passed, skipped,
                              error=error, db_path=self._db_path)
        except Exception as exc:
            print(f"[PVD] DB close error: {exc}")

    def _safe_abort(self) -> None:
        if self._drv is None:
            return
        try:
            if self._drv.connected:
                self._drv.abort()
        except Exception:
            pass


# ── Baseline promotion ────────────────────────────────────────────────────────

def promote_baseline(run_id: int, tol_pct: Optional[float] = None,
                     db_path: str = db.DB_PATH,
                     profiles_path: str = _PROFILES_PATH) -> dict:
    """
    Write the measurements from a completed run into the profile as nominals.

    This is the step that turns a profile from "records numbers" into "issues
    verdicts", so it is deliberately explicit rather than automatic. Only points
    that actually produced a clean measurement are promoted; a point that failed
    or was skipped keeps whatever nominal it had.

    Returns a summary of what changed.
    """
    run = db.get_pvd_run(run_id, db_path=db_path)
    if not run:
        raise ValueError(f"No PVD run #{run_id}")

    data = load_profiles(profiles_path)
    profile_id = run["profile_id"]
    if profile_id not in data.get("profiles", {}):
        raise ValueError(f"Profile '{profile_id}' no longer exists in pvd_profiles.json")

    profile = data["profiles"][profile_id]
    by_id = {p["id"]: p for p in profile.get("points", [])}

    promoted, skipped = [], []
    for pt in run.get("points", []):
        target = by_id.get(pt["point_id"])
        if target is None:
            skipped.append({"point_id": pt["point_id"], "why": "not in profile"})
            continue
        if pt["measured"] is None or pt["verdict"] == SKIPPED or pt["status_flags"]:
            skipped.append({"point_id": pt["point_id"],
                            "why": "no clean measurement to promote"})
            continue
        target["nominal"] = pt["measured"]
        if tol_pct is not None:
            target["tol_pct"] = float(tol_pct)
        promoted.append({"point_id": pt["point_id"], "nominal": pt["measured"]})

    profile["baselined_from_run"] = run_id
    profile["baselined_at"] = run.get("finished_at") or run.get("started_at")
    profile["baselined_on_instrument"] = {
        "model": run.get("device_model"),
        "serial": run.get("device_serial"),
        "firmware": run.get("firmware"),
    }

    tmp = profiles_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, profiles_path)

    return {"profile_id": profile_id, "promoted": promoted, "skipped": skipped}


# ── Verification currency ─────────────────────────────────────────────────────

def verification_status(profile_id: str = "", db_path: str = db.DB_PATH,
                        profiles_path: str = _PROFILES_PATH) -> dict:
    """
    Report when the instrument last passed a verification and whether that is
    still inside the profile's recommended interval.

    Purely informational — nothing in this codebase blocks a production test on
    the strength of it. That call belongs to whoever owns the quality procedure,
    not to this module.
    """
    last = db.get_last_passed_pvd(profile_id, db_path=db_path)
    interval_h = None
    if profile_id:
        try:
            interval_h = get_profile(profile_id, profiles_path).get(
                "recommended_interval_hours")
        except Exception:
            interval_h = None

    if not last:
        return {"verified": False, "last_passed_at": None, "run_id": None,
                "age_hours": None, "interval_hours": interval_h, "due": True}

    import datetime
    try:
        ts = datetime.datetime.fromisoformat(last["finished_at"] or last["started_at"])
        age_h = (datetime.datetime.now() - ts).total_seconds() / 3600.0
    except Exception:
        age_h = None

    due = bool(interval_h and age_h is not None and age_h > float(interval_h))
    return {
        "verified": True,
        "last_passed_at": last.get("finished_at") or last.get("started_at"),
        "run_id": last.get("id"),
        "operator": last.get("operator"),
        "age_hours": round(age_h, 2) if age_h is not None else None,
        "interval_hours": interval_h,
        "due": due,
    }
