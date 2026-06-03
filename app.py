"""
app.py
------
Juniper Automated Test Station — multi-device HMI.

Architecture:
  The LOGO! PLC + thermal rig is ALWAYS-ON background infrastructure. It
  runs continuously and records sensor data regardless of which instrument
  (HiPot or DC Load) is active or whether any instrument is connected at
  all. The PLC manages chamber environment (heaters, vents, servos) and
  safety interlocks while the active instrument performs electrical testing.

  Instrument selection is mutually exclusive (only one at a time), but the
  thermal rig runs in parallel with any instrument test. Sensor data is
  continuously recorded to the database, and instrument test events are
  annotated into the sensor timeline so results can be correlated.

Instruments (choose one at a time):
  • Vitrek V71 HiPot Tester       (USB HID-to-UART)
  • Siglent SDL1020X-E DC Load    (TCP/LAN or USB CDC)
  • None (thermal monitoring only)

Background infrastructure (always on):
  • Siemens LOGO! PLC             (Modbus TCP)
  • Thermal controller            (RPi GPIO + MAX31855 thermocouples)
  • Continuous sensor recorder    (writes to SQLite every second)

Routes:
  GET  /                       Landing page
  GET  /hipot                  HiPot instrument page
  GET  /dcload                 DC Load instrument page
  GET  /thermal                Thermal rig monitoring page (always visible)

  POST /api/rig/connect        Connect PLC + thermal rig (call at startup)
  POST /api/rig/disconnect     Disconnect PLC + thermal rig
  GET  /api/rig/status         Full rig status (PLC I/O + temps + heater)

  POST /api/instrument/connect   Connect a test instrument (hipot | dcload)
  POST /api/instrument/disconnect Disconnect active instrument
  GET  /api/instrument/status    Active instrument status

  POST /api/hipot/run          HiPot: run test sequence
  POST /api/hipot/abort        HiPot: abort
  POST /api/hipot/cont         HiPot: continue HOLD
  GET  /api/hipot/live         HiPot: live measurements

  GET  /api/dcload/measure     DC Load: live measurements
  POST /api/dcload/configure   DC Load: set mode + setpoint
  POST /api/dcload/input       DC Load: input on/off

  GET  /api/thermal/status     Thermal rig status snapshot
  POST /api/thermal/setpoint   Set target temperature
  POST /api/thermal/vent       Set vent position
  POST /api/thermal/control    Start/stop PID loop
  POST /api/plc/output         Set named PLC output
  GET  /api/plc/io             Read all PLC I/O

  GET  /api/sessions           Test history
  GET  /api/session/<id>       Session detail
  GET  /api/export             Export all sessions to Excel
  GET  /api/export/<id>        Export single session to Excel

Run:  python app.py
"""

import os
import json
import threading
import time
import datetime
import tempfile

from flask import (Flask, jsonify, request, send_file,
                   render_template_string, redirect, url_for)

import database as db
from excel_export import export_to_excel
from pec0063_test import PEC0063Test, evaluate_ul

# Drivers — loaded lazily so the app starts even without hardware
try:
    from v71_driver import V71Driver, V71Error
    V71_AVAILABLE = True
except Exception:
    V71Driver = None; V71Error = Exception; V71_AVAILABLE = False

try:
    from sdl1020x_driver import SDL1020XDriver, SDL1020XError
    SDL_AVAILABLE = True
except Exception:
    SDL1020XDriver = None; SDL1020XError = Exception; SDL_AVAILABLE = False

try:
    from plc.logo_driver import LogoDriver, LogoError
    LOGO_AVAILABLE = True
except Exception:
    LogoDriver = None; LogoError = Exception; LOGO_AVAILABLE = False

try:
    from plc.thermal_controller import ThermalController
    THERMAL_AVAILABLE = True
except Exception:
    ThermalController = None; THERMAL_AVAILABLE = False

# Load rig config
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "plc", "rig_config.json")
try:
    with open(_CONFIG_PATH) as _f:
        RIG_CONFIG = json.load(_f)
except Exception:
    RIG_CONFIG = {}

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# ── Global state ──────────────────────────────────────────────────────────────
_lock             = threading.Lock()

# Background infrastructure — always on
_plc:     "LogoDriver | None"        = None
_thermal: "ThermalController | None" = None
_rig_connected = False

# Active instrument — one at a time
_active_instrument: str | None = None   # "hipot" | "dcload" | None
_hipot:   "V71Driver | None"       = None
_dcload:  "SDL1020XDriver | None"  = None

# HiPot session tracking
_hipot_session_id: int | None = None
_hipot_step_types: list[str]  = []
_hipot_run_thread: threading.Thread | None = None

# PEC-0063 thermal qualification test
_pec0063_test = None   # PEC0063Test instance when running

# Continuous sensor recorder
_recorder_thread: threading.Thread | None = None
_recorder_running = False


# ── Continuous sensor recorder ────────────────────────────────────────────────
def _start_recorder():
    """Start background thread that logs all sensor data every second."""
    global _recorder_thread, _recorder_running
    if _recorder_running:
        return
    _recorder_running = True
    _recorder_thread = threading.Thread(target=_recorder_loop, daemon=True)
    _recorder_thread.start()


def _stop_recorder():
    global _recorder_running
    _recorder_running = False


def _recorder_loop():
    """
    Records a snapshot every second into the sensor_log table.
    This runs independently of any instrument test — it's always ticking
    so thermal data, PLC I/O states, and instrument state are all correlated
    in the database timeline.
    """
    db.init_db()
    db.ensure_sensor_log_table()
    while _recorder_running:
        try:
            snap = {
                "ts": datetime.datetime.now().isoformat(),
                "instrument": _active_instrument,
                "hipot_running": _hipot is not None and _hipot.connected and _hipot.is_running() if _hipot else False,
                "hipot_session": _hipot_session_id,
                "dcload_input_on": None,
            }
            # Thermal readings
            if _thermal:
                s = _thermal.status()
                snap.update({
                    "tc1_c": s["temps"].get("TC1_AMBIENT"),
                    "tc2_c": s["temps"].get("TC2_DUT"),
                    "tc3_c": s["temps"].get("TC3_HEATER"),
                    "tc4_c": s["temps"].get("TC4_EXHAUST"),
                    "heater_duty": s["heater_duty_pct"],
                    "setpoint_c":  s["setpoint_c"],
                    "vent_a_pct":  s["vent_position"].get("A"),
                    "vent_b_pct":  s["vent_position"].get("B"),
                    "control_active": s["control_active"],
                    "thermal_fault": s["fault"],
                })
            # PLC I/O
            if _plc and _plc.connected:
                try:
                    inputs = _plc.get_all_inputs()
                    snap["plc_estop"]    = inputs.get("ESTOP")
                    snap["plc_door"]     = inputs.get("DOOR_INTERLOCK")
                    snap["plc_overtemp"] = inputs.get("OVERTEMP_CUT")
                except Exception:
                    pass
            # DC load live measurement
            if _dcload and _dcload.connected:
                try:
                    m = _dcload.measure_all()
                    snap["dcload_v"]    = m["voltage_v"]
                    snap["dcload_a"]    = m["current_a"]
                    snap["dcload_w"]    = m["power_w"]
                    snap["dcload_ohm"]  = m["resistance_ohm"]
                    snap["dcload_input_on"] = _dcload.is_input_on()
                except Exception:
                    pass

            db.log_sensor_snapshot(snap)
        except Exception:
            pass
        time.sleep(1.0)


# ── Background run monitor for HiPot ─────────────────────────────────────────
def _hipot_monitor(session_id: int, step_types: list[str]):
    global _hipot_session_id
    try:
        time.sleep(0.5)
        while _hipot and _hipot.connected and _hipot.is_running():
            time.sleep(0.25)
        if not _hipot or not _hipot.connected:
            return
        overall = _hipot.overall_result()
        db.finish_session(session_id, overall)
        for i, ch in enumerate(_hipot.step_status_string(), start=1):
            step_type = step_types[i - 1] if i <= len(step_types) else "UNKNOWN"
            result = _hipot.step_result(i)
            db.save_step_result(session_id, i, step_type, result)
    except Exception as e:
        app.logger.error(f"HiPot monitor error: {e}")
    finally:
        _hipot_session_id = None


# ═══════════════════════════════════════════════════════════════════════════════
# RIG API  (background infrastructure — always on, independent of instruments)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/rig/connect", methods=["POST"])
def api_rig_connect():
    """
    Connect the PLC and thermal controller. Call once at station startup.
    This is independent of instrument connections — the rig stays connected
    even when switching instruments or when no instrument is active.
    """
    global _plc, _thermal, _rig_connected
    d = request.get_json(force=True)
    errors = []

    if LOGO_AVAILABLE:
        plc_cfg = RIG_CONFIG.get("plc", {})
        try:
            if _plc:
                _plc.disconnect()
            plc = LogoDriver()
            plc.connect(d.get("plc_host", plc_cfg.get("host", "192.168.1.100")),
                        int(d.get("plc_port", plc_cfg.get("port", 502))))
            _plc = plc
        except Exception as e:
            errors.append(f"PLC connect failed: {e}")

    if THERMAL_AVAILABLE:
        try:
            if _thermal:
                _thermal.shutdown()
            _transport = RIG_CONFIG.get("transport", "auto")
            _esp = RIG_CONFIG.get("esp8266", {})
            tc = ThermalController(
                plc=_plc,
                transport=_transport,
                esp_host=_esp.get("host", "192.168.1.60"),
                esp_port=_esp.get("port", 80),
            )
            tc.init()
            _thermal = tc
        except Exception as e:
            errors.append(f"Thermal init failed: {e}")

    db.init_db()
    _rig_connected = True
    _start_recorder()   # Begin continuous sensor logging

    return jsonify({
        "ok": True,
        "plc_connected":  _plc is not None and _plc.connected,
        "thermal_ready":  _thermal is not None,
        "recording":      _recorder_running,
        "warnings": errors,
    })


@app.route("/api/rig/disconnect", methods=["POST"])
def api_rig_disconnect():
    global _plc, _thermal, _rig_connected
    _stop_recorder()
    if _thermal:
        try:
            _thermal.shutdown()
        except Exception:
            pass
        _thermal = None
    if _plc:
        try:
            _plc.disconnect()
        except Exception:
            pass
        _plc = None
    _rig_connected = False
    return jsonify({"ok": True})


@app.route("/api/rig/status")
def api_rig_status():
    status = {
        "rig_connected":  _rig_connected,
        "plc_connected":  _plc is not None and _plc.connected,
        "thermal_ready":  _thermal is not None,
        "recording":      _recorder_running,
        "active_instrument": _active_instrument,
    }
    if _thermal:
        status["thermal"] = _thermal.status()
    if _plc and _plc.connected:
        try:
            status["plc_inputs"]  = _plc.get_all_inputs()
            status["plc_outputs"] = _plc.get_all_outputs()
            status["plc_safe"], status["plc_faults"] = _plc.is_safe_to_run()
        except Exception as e:
            status["plc_error"] = str(e)
    return jsonify({"ok": True, **status})


# ═══════════════════════════════════════════════════════════════════════════════
# INSTRUMENT API  (mutually exclusive — one instrument at a time)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/instrument/connect", methods=["POST"])
def api_instrument_connect():
    """
    Connect a test instrument (hipot or dcload).
    The rig (PLC/thermal) continues running regardless.
    Only one instrument can be active at a time.
    """
    global _active_instrument, _hipot, _dcload
    d = request.get_json(force=True)
    instrument = d.get("instrument", "").lower()

    with _lock:
        _disconnect_instrument()
        try:
            if instrument == "hipot":
                if not V71_AVAILABLE:
                    return jsonify({"ok": False, "error": "V71 driver not available"}), 500
                drv = V71Driver()
                mode = d.get("mode", "usb")
                if mode == "usb":
                    drv.connect_usb()
                else:
                    drv.connect_serial(d.get("port", "COM1"), int(d.get("baud", 115200)))
                _hipot = drv
                _active_instrument = "hipot"
                db.init_db()
                idn = drv.identify()
                return jsonify({"ok": True, "instrument": "hipot", "idn": idn})

            elif instrument == "dcload":
                if not SDL_AVAILABLE:
                    return jsonify({"ok": False, "error": "SDL driver not available"}), 500
                drv = SDL1020XDriver()
                mode = d.get("mode", "tcp")
                if mode == "tcp":
                    drv.connect_tcp(d.get("host", "192.168.1.101"),
                                    int(d.get("port", 5025)))
                else:
                    drv.connect_serial(d.get("port", "COM5"))
                _dcload = drv
                _active_instrument = "dcload"
                idn = drv.identify()
                return jsonify({"ok": True, "instrument": "dcload", "idn": idn})

            else:
                return jsonify({"ok": False, "error": f"Unknown instrument '{instrument}'. Use 'hipot' or 'dcload'."}), 400

        except Exception as e:
            _disconnect_instrument()
            return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/instrument/disconnect", methods=["POST"])
def api_instrument_disconnect():
    with _lock:
        _disconnect_instrument()
    return jsonify({"ok": True})


def _disconnect_instrument():
    global _hipot, _dcload, _active_instrument
    for obj in (_hipot, _dcload):
        if obj and hasattr(obj, "disconnect"):
            try:
                obj.disconnect()
            except Exception:
                pass
    _hipot = _dcload = None
    _active_instrument = None


# Legacy aliases — keep old /api/connect working for backwards compat
@app.route("/api/connect", methods=["POST"])
def api_connect_legacy():
    d = request.get_json(force=True)
    if d.get("instrument") in ("hipot", "dcload"):
        return api_instrument_connect()
    return api_rig_connect()


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect_legacy():
    with _lock:
        _disconnect_instrument()
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    return jsonify({
        "rig_connected":     _rig_connected,
        "active_instrument": _active_instrument,
        "hipot_connected":   _hipot is not None and _hipot.connected,
        "dcload_connected":  _dcload is not None and _dcload.connected,
        "plc_connected":     _plc is not None and _plc.connected,
        "thermal_ready":     _thermal is not None,
        "recording":         _recorder_running,
        "hipot_session":     _hipot_session_id,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# HIPOT API (unchanged from previous, condensed)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/hipot/run", methods=["POST"])
def api_hipot_run():
    global _hipot_session_id, _hipot_step_types, _hipot_run_thread
    if not _hipot or not _hipot.connected:
        return jsonify({"ok": False, "error": "HiPot not connected"}), 400
    d = request.get_json(force=True)
    steps = d.get("steps", [])
    if not steps:
        return jsonify({"ok": False, "error": "No steps provided"}), 400
    try:
        idn = _hipot.identify()
        _hipot.new_sequence()
        step_types = []
        for step in steps:
            st = step.get("type", "").upper()
            step_types.append(st)
            _add_hipot_step(_hipot, st, step)
        session_id = db.create_session(
            operator=d.get("operator", ""), part_number=d.get("part_number", ""),
            serial_number=d.get("serial_number", ""), notes=d.get("notes", ""),
            device_model=idn.get("model", ""), device_serial=idn.get("serial", ""),
            firmware=idn.get("firmware", ""),
        )
        _hipot_session_id = session_id
        _hipot_step_types = step_types
        _hipot.run()
        _hipot_run_thread = threading.Thread(
            target=_hipot_monitor, args=(session_id, step_types), daemon=True
        )
        _hipot_run_thread.start()
        return jsonify({"ok": True, "session_id": session_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _add_hipot_step(drv, st, step):
    if st == "ACW":
        drv.add_acw_step(float(step["voltage"]), float(step.get("ramp", 1.5)),
                         float(step.get("dwell", 60)),
                         float(step.get("max_leakage", 0.005)),
                         float(step["min_leakage"]) if step.get("min_leakage") else None,
                         bool(step.get("grounded", False)))
    elif st == "DCW":
        drv.add_dcw_step(float(step["voltage"]), float(step.get("ramp", 1.5)),
                         float(step.get("dwell", 60)),
                         float(step.get("max_leakage", 25e-6)),
                         float(step["min_leakage"]) if step.get("min_leakage") else None,
                         bool(step.get("grounded", False)), bool(step.get("capacitive", False)))
    elif st == "IR":
        drv.add_ir_step(float(step["voltage"]), float(step.get("dwell", 60)),
                        float(step.get("min_resistance", 100e6)),
                        float(step["max_resistance"]) if step.get("max_resistance") else None,
                        float(step.get("precheck_delay", 0)), bool(step.get("grounded", False)))
    elif st == "GB":
        drv.add_gb_step(float(step["current"]), float(step.get("dwell", 5)),
                        float(step.get("max_ohm", 0.1)),
                        float(step["min_ohm"]) if step.get("min_ohm") else None)
    elif st == "CONT":
        drv.add_cont_step(float(step.get("dwell", 5)),
                          float(step["min_ohm"]) if step.get("min_ohm") else None,
                          float(step["max_ohm"]) if step.get("max_ohm") else None)
    else:
        raise ValueError(f"Unknown step type: {st}")


@app.route("/api/hipot/abort", methods=["POST"])
def api_hipot_abort():
    if not _hipot or not _hipot.connected:
        return jsonify({"ok": False, "error": "Not connected"}), 400
    _hipot.abort()
    return jsonify({"ok": True})


@app.route("/api/hipot/cont", methods=["POST"])
def api_hipot_cont():
    if not _hipot or not _hipot.connected:
        return jsonify({"ok": False, "error": "Not connected"}), 400
    _hipot.cont()
    return jsonify({"ok": True})


@app.route("/api/hipot/status")
def api_hipot_status():
    if not _hipot or not _hipot.connected:
        return jsonify({"connected": False})
    try:
        running = _hipot.is_running()
        return jsonify({
            "connected": True, "running": running,
            "current_step": _hipot.current_step() if running else 0,
            "step_status":  _hipot.step_status_string() if running else "",
            "session_id": _hipot_session_id,
        })
    except Exception as e:
        return jsonify({"connected": False, "error": str(e)})


@app.route("/api/hipot/live")
def api_hipot_live():
    if not _hipot or not _hipot.connected or not _hipot.is_running():
        return jsonify({"ok": False})
    results = {}
    for qty in ("AMPS", "VOLTS", "OHMS"):
        try:
            results[qty.lower()] = _hipot.live_measurement(qty)
        except Exception:
            results[qty.lower()] = None
    return jsonify({"ok": True, **results})


# ═══════════════════════════════════════════════════════════════════════════════
# DC LOAD API
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/dcload/measure")
def api_dcload_measure():
    if not _dcload or not _dcload.connected:
        return jsonify({"ok": False, "error": "DC Load not connected"}), 400
    try:
        return jsonify({"ok": True, **_dcload.measure_all(),
                        "mode": _dcload.get_mode(),
                        "input_on": _dcload.is_input_on()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dcload/configure", methods=["POST"])
def api_dcload_configure():
    if not _dcload or not _dcload.connected:
        return jsonify({"ok": False, "error": "DC Load not connected"}), 400
    d = request.get_json(force=True)
    mode = d.get("mode", "CC").upper()
    value = float(d.get("value", 0))
    try:
        _dcload.set_mode(mode)
        if mode == "CC": _dcload.set_current(value)
        elif mode == "CV": _dcload.set_voltage(value)
        elif mode == "CR": _dcload.set_resistance(value)
        elif mode == "CP": _dcload.set_power(value)
        return jsonify({"ok": True, "mode": mode, "value": value})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dcload/input", methods=["POST"])
def api_dcload_input():
    if not _dcload or not _dcload.connected:
        return jsonify({"ok": False, "error": "DC Load not connected"}), 400
    d = request.get_json(force=True)
    on = bool(d.get("on", False))
    try:
        if on:
            _dcload.input_on()
        else:
            _dcload.input_off()
        return jsonify({"ok": True, "input_on": on})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# THERMAL RIG / PLC API
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/thermal/status")
def api_thermal_status():
    if not _thermal:
        return jsonify({"ok": False, "error": "Thermal rig not initialised"}), 400
    try:
        s = _thermal.status()
        if _plc and _plc.connected:
            safe, reasons = _plc.is_safe_to_run()
            s["plc_safe"] = safe
            s["plc_faults"] = reasons
            s["plc_inputs"] = _plc.get_all_inputs()
        return jsonify({"ok": True, **s})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/thermal/setpoint", methods=["POST"])
def api_thermal_setpoint():
    if not _thermal:
        return jsonify({"ok": False, "error": "Thermal rig not initialised"}), 400
    d = request.get_json(force=True)
    temp = float(d.get("temp_c", 25.0))
    try:
        _thermal.set_setpoint(temp)
        return jsonify({"ok": True, "setpoint_c": temp})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/thermal/vent", methods=["POST"])
def api_thermal_vent():
    if not _thermal:
        return jsonify({"ok": False, "error": "Thermal rig not initialised"}), 400
    d = request.get_json(force=True)
    vent = d.get("vent", "A")
    pct  = float(d.get("percent", 0))
    try:
        _thermal.set_vent_position(vent, pct)
        return jsonify({"ok": True, "vent": vent, "percent": pct})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/thermal/control", methods=["POST"])
def api_thermal_control():
    if not _thermal:
        return jsonify({"ok": False, "error": "Thermal rig not initialised"}), 400
    d = request.get_json(force=True)
    start = bool(d.get("start", False))
    try:
        if start:
            _thermal.start_control_loop()
        else:
            _thermal.stop_control_loop()
        return jsonify({"ok": True, "control_active": start})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/plc/output", methods=["POST"])
def api_plc_output():
    if not _plc or not _plc.connected:
        return jsonify({"ok": False, "error": "PLC not connected"}), 400
    d = request.get_json(force=True)
    name  = d.get("name", "")
    state = bool(d.get("state", False))
    try:
        _plc.set_named_output(name, state)
        return jsonify({"ok": True, "name": name, "state": state})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/plc/io")
def api_plc_io():
    if not _plc or not _plc.connected:
        return jsonify({"ok": False, "error": "PLC not connected"}), 400
    try:
        return jsonify({
            "ok": True,
            "inputs":      _plc.get_all_inputs(),
            "outputs":     _plc.get_all_outputs(),      # actual Q relay states
            "sw_enables":  _plc.get_all_sw_enables(),   # M-marker software requests
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# HISTORY / EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/sensor_log")
def api_sensor_log():
    """Return recent sensor log rows (or rows for a specific session)."""
    db.init_db(); db.ensure_sensor_log_table()
    session_id = request.args.get("session_id", type=int)
    limit      = request.args.get("limit", 3600, type=int)
    rows = db.get_sensor_log(session_id=session_id, limit=limit)
    return jsonify({"ok": True, "rows": rows, "count": len(rows)})


@app.route("/api/sessions")
def api_sessions():
    db.init_db()
    rows = db.get_sessions(limit=int(request.args.get("limit", 50)))
    return jsonify({"ok": True, "sessions": rows, "stats": db.get_stats()})


@app.route("/api/session/<int:session_id>")
def api_session(session_id):
    session = db.get_session(session_id)
    if not session:
        return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True, "session": session, "steps": db.get_steps(session_id)})


# ═══════════════════════════════════════════════════════════════════════════════
# PEC-0063 THERMAL QUALIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/pec0063/housings")
def api_pec0063_housings():
    """Return the list of housing configs and baseline results from rig_config.json."""
    pec_cfg = RIG_CONFIG.get("pec0063_thermal_qualification", {})
    return jsonify({
        "ok":      True,
        "housings": pec_cfg.get("housings", {}),
        "dc_load":  pec_cfg.get("dc_load", {}),
    })


@app.route("/api/pec0063/start", methods=["POST"])
def api_pec0063_start():
    global _pec0063_test
    if _active_instrument not in (None, "dcload"):
        return jsonify({"ok": False, "error": "A different instrument is active — disconnect it first."}), 400

    d = request.get_json(force=True)
    housing  = d.get("housing_key", "DSK_Single")
    standard = d.get("standard", "UL_1310")
    surface  = d.get("surface", "nonmetallic")

    # Validate housing key
    pec_cfg  = RIG_CONFIG.get("pec0063_thermal_qualification", {})
    if housing not in pec_cfg.get("housings", {}):
        return jsonify({"ok": False,
                        "error": f"Unknown housing '{housing}'. Valid: {list(pec_cfg['housings'])}"}), 400

    if _pec0063_test and _pec0063_test.status.state == "running":
        return jsonify({"ok": False, "error": "A PEC-0063 test is already running."}), 400

    conn = db.get_connection()
    _pec0063_test = PEC0063Test(
        thermal_ctrl = _thermal,
        dc_load      = _dcload,
        db           = conn,
        housing_key  = housing,
        standard     = standard,
        surface      = surface,
    )
    try:
        _pec0063_test.start()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "housing": housing, "standard": standard, "surface": surface})


@app.route("/api/pec0063/status")
def api_pec0063_status():
    if not _pec0063_test:
        return jsonify({"ok": True, "state": "idle"})
    return jsonify({"ok": True, **_pec0063_test.get_status()})


@app.route("/api/pec0063/stop", methods=["POST"])
def api_pec0063_stop():
    if not _pec0063_test or _pec0063_test.status.state not in ("running", "steady_state"):
        return jsonify({"ok": False, "error": "No test running."}), 400
    _pec0063_test.stop()
    return jsonify({"ok": True, "state": _pec0063_test.status.state})


@app.route("/api/pec0063/results")
def api_pec0063_results():
    """Return all saved thermal qualification test results from the database."""
    db.ensure_sensor_log_table()
    rows = db.get_thermal_tests(limit=int(request.args.get("limit", 200)))
    return jsonify({"ok": True, "results": rows, "count": len(rows)})


@app.route("/api/pec0063/evaluate", methods=["POST"])
def api_pec0063_evaluate():
    """One-shot UL evaluation for a given Tcase and ambient — no hardware needed."""
    d = request.get_json(force=True)
    try:
        result = evaluate_ul(
            tcase_c  = float(d["tcase_c"]),
            ambient_c= float(d["ambient_c"]),
            standard = d.get("standard", "UL_1310"),
            surface  = d.get("surface", "nonmetallic"),
        )
        return jsonify({"ok": True, "evaluation": result})
    except (KeyError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/export")
def api_export():
    db.init_db()
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    export_to_excel(tmp.name)
    return send_file(tmp.name, as_attachment=True,
                     download_name=f"hipot_results_{datetime.date.today()}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/export/<int:session_id>")
def api_export_session(session_id):
    db.init_db()
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    export_to_excel(tmp.name, session_ids=[session_id])
    return send_file(tmp.name, as_attachment=True,
                     download_name=f"hipot_session_{session_id}_{datetime.date.today()}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE ROUTES  (serve the Juniper-branded single-page UIs)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def page_landing():
    return render_template_string(_LANDING_HTML)

@app.route("/hipot")
def page_hipot():
    return render_template_string(_HIPOT_HTML)

@app.route("/dcload")
def page_dcload():
    return render_template_string(_DCLOAD_HTML)

@app.route("/thermal")
def page_thermal():
    return render_template_string(_THERMAL_HTML)


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED HTML CHROME  (Juniper brand bar + nav shared by all pages)
# ═══════════════════════════════════════════════════════════════════════════════

_HTML_HEAD = r"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s — Juniper Test Station</title>
<link rel="icon" type="image/svg+xml" href="/static/assets/juniper-logo.svg">
<link rel="stylesheet" href="/static/css/juniper-brand.css">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
body{min-height:100vh;display:flex;flex-direction:column;background:var(--bg);color:var(--text);font-family:var(--juniper-font);}
.app-main{flex:1;max-width:1440px;margin:0 auto;padding:16px;width:100%;}
/* Instrument nav tabs */
.inst-nav{display:flex;gap:6px;padding:8px 16px;background:var(--bg-elev-2);border-bottom:1px solid var(--border);}
.inst-tab{padding:6px 18px;border-radius:6px 6px 0 0;border:1px solid var(--border);border-bottom:none;font-size:.82rem;font-weight:600;text-decoration:none;color:var(--text-soft);background:var(--bg-elev);transition:background .15s;}
.inst-tab:hover{background:var(--bg-elev-2);color:var(--text);}
.inst-tab.active{background:var(--primary);color:#fff;border-color:var(--primary);}
.inst-tab.connected::after{content:" ●";color:#4CAF50;font-size:.7rem;}
/* Cards */
.card{background:var(--bg-elev);border:1px solid var(--border);border-radius:10px;padding:16px;box-shadow:0 2px 8px var(--shadow);margin-bottom:14px;}
.card h2{font-size:.88rem;font-weight:600;color:var(--primary);margin-bottom:12px;border-bottom:2px solid var(--border);padding-bottom:6px;letter-spacing:.04em;text-transform:uppercase;}
/* Forms */
label{display:block;font-size:.78rem;font-weight:600;margin-top:8px;margin-bottom:2px;color:var(--text-soft);}
input,select{width:100%;padding:6px 9px;border:1px solid var(--input-border);border-radius:5px;font-size:.86rem;background:var(--input-bg);color:var(--text);font-family:var(--juniper-font);}
input:focus,select:focus{outline:2px solid var(--primary);outline-offset:1px;}
/* Buttons */
button{padding:7px 14px;border:none;border-radius:5px;cursor:pointer;font-size:.86rem;font-weight:600;margin-top:6px;font-family:var(--juniper-font);transition:opacity .15s;}
button:disabled{opacity:.45;cursor:default;}
.btn-primary{background:var(--primary);color:#fff;}
.btn-primary:hover:not(:disabled){background:var(--primary-hover);}
.btn-green{background:var(--success);color:#fff;}
.btn-green:hover:not(:disabled){background:var(--success-hover);}
.btn-red{background:var(--error-fg);color:#fff;}
.btn-muted{background:var(--bg-elev-2);color:var(--text-soft);border:1px solid var(--border);}
.btn-orange{background:#c46200;color:#fff;}
/* Grid helpers */
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
.grid-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;}
.btn-row{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;}
/* Live value tiles */
.live-tile{background:var(--bg-elev-2);border:1px solid var(--border);border-radius:8px;padding:12px;text-align:center;}
.live-tile .lv-label{font-size:.7rem;color:var(--text-muted);font-weight:600;letter-spacing:.06em;text-transform:uppercase;}
.live-tile .lv-val{font-size:1.35rem;font-weight:700;color:var(--primary);margin-top:2px;}
/* Status / messages */
.status-pill{font-size:.72rem;font-weight:700;padding:3px 10px;border-radius:10px;background:rgba(255,255,255,.15);color:var(--juniper-offwhite);letter-spacing:.04em;text-transform:uppercase;}
.status-pill.ok{background:rgba(46,160,71,.7);}
.status-pill.warn{background:rgba(196,98,0,.7);}
.msg{padding:8px 12px;border-radius:5px;margin-top:8px;font-size:.82rem;border:1px solid transparent;}
.msg.ok{background:var(--success-hover);color:#fff;}
.msg.err{background:var(--error-bg);color:var(--error-fg);border-color:var(--error-border);}
.msg.info{background:var(--tpl-summary-bg);color:var(--text);border-color:var(--tpl-summary-border);}
/* Step badges */
.step-badge{padding:3px 7px;border-radius:4px;font-size:.75rem;font-weight:700;background:var(--bg-elev-2);color:var(--text-muted);border:1px solid var(--border);}
.step-badge.P{background:#d4edda;color:#155724;}
.step-badge.F{background:var(--error-bg);color:var(--error-fg);}
.step-badge.running{background:var(--warning-bg);color:var(--warning-fg);}
/* Step item */
.step-item{background:var(--bg-elev-2);border:1px solid var(--border);border-radius:6px;padding:10px;margin-bottom:8px;position:relative;}
.step-item h4{font-size:.8rem;color:var(--primary);margin-bottom:6px;font-weight:600;}
.step-item .del-step{position:absolute;top:6px;right:8px;background:none;border:none;color:var(--error-fg);font-size:1rem;cursor:pointer;padding:0;}
/* Results table */
.results-table{width:100%;border-collapse:collapse;font-size:.8rem;}
.results-table th{background:var(--table-head-bg);color:var(--text-soft);padding:6px 8px;text-align:left;font-weight:600;font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;border-bottom:2px solid var(--border);}
.results-table td{padding:5px 8px;border-bottom:1px solid var(--hairline);}
.results-table tr.pass td{background:rgba(46,127,58,.08);}
.results-table tr.fail td{background:rgba(218,54,51,.07);}
.tag-pass{color:var(--success);font-weight:700;}
.tag-fail{color:var(--error-fg);font-weight:700;}
/* Two-column layout */
.layout-2col{display:grid;grid-template-columns:310px 1fr;gap:14px;}
.col-left,.col-right{display:flex;flex-direction:column;gap:14px;}
/* Gauge bar */
.gauge-bar{height:10px;border-radius:5px;background:var(--border);overflow:hidden;margin-top:4px;}
.gauge-fill{height:100%;background:var(--primary);border-radius:5px;transition:width .3s;}
/* Thermal temp grid */
.temp-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
.temp-tile{background:var(--bg-elev-2);border:1px solid var(--border);border-radius:8px;padding:10px;}
.temp-tile .tc-label{font-size:.72rem;color:var(--text-muted);font-weight:600;}
.temp-tile .tc-desc{font-size:.7rem;color:var(--text-muted);margin-bottom:4px;}
.temp-tile .tc-val{font-size:1.6rem;font-weight:700;color:var(--primary);}
.temp-tile .tc-val.hot{color:#c46200;}
.temp-tile .tc-val.alarm{color:var(--error-fg);}
/* PLC I/O table */
.io-table{display:grid;grid-template-columns:1fr 1fr;gap:6px;}
.io-row{display:flex;justify-content:space-between;align-items:center;padding:4px 8px;background:var(--bg-elev-2);border:1px solid var(--border);border-radius:4px;font-size:.78rem;}
.io-led{width:10px;height:10px;border-radius:50%;background:#555;}
.io-led.on{background:#4CAF50;}
.io-led.off{background:#888;}
</style>
</head>
<body>
<header class="juniper-brand-bar">
  <div class="juniper-brand-bar-inner">
    <a href="/"><img class="juniper-logo" src="/static/assets/juniper-logo.svg" alt="Juniper"></a>
    <div style="display:flex;align-items:center;gap:14px;">
      <span class="juniper-product">Automated Test Station</span>
      <span class="status-pill" id="global-status">No instrument</span>
      <button id="juniper-theme-toggle" class="juniper-theme-toggle" type="button" aria-label="Toggle theme"><span class="juniper-theme-icon">🌙</span></button>
    </div>
  </div>
</header>
<nav class="inst-nav">
  <a href="/"       class="inst-tab %(tab_home)s"   >⌂ Station</a>
  <a href="/hipot"  class="inst-tab %(tab_hipot)s"  >⚡ HiPot (V71)</a>
  <a href="/dcload" class="inst-tab %(tab_dcload)s" >🔋 DC Load (SDL1020X)</a>
  <a href="/thermal"class="inst-tab %(tab_thermal)s">🌡 Thermal Rig</a>
</nav>
"""

_HTML_FOOT = r"""
<footer class="juniper-footer">Designed and built by <a href="https://juniperdesign.com" target="_blank" rel="noopener">Juniper Design</a></footer>
<script src="/static/js/juniper-theme.js"></script>
%(extra_js)s
<script>
// Poll global status and update nav pill
(async function pollGlobal(){
  try{
    const j=await(await fetch('/api/status')).json();
    const pill=document.getElementById('global-status');
    const inst=j.active_instrument;
    if(inst){pill.textContent=inst.toUpperCase()+' CONNECTED';pill.className='status-pill ok';}
    else{pill.textContent='No instrument';pill.className='status-pill';}
    // Mark connected tabs
    document.querySelectorAll('.inst-tab').forEach(t=>{
      t.classList.remove('connected');
      if(inst==='hipot'&&t.href.endsWith('/hipot'))t.classList.add('connected');
      if(inst==='dcload'&&t.href.endsWith('/dcload'))t.classList.add('connected');
      if(inst==='thermal'&&t.href.endsWith('/thermal'))t.classList.add('connected');
    });
  }catch(e){}
  setTimeout(pollGlobal,2000);
})();
</script>
</body></html>"""


# ── Landing page ───────────────────────────────────────────────────────────────
_LANDING_HTML = _HTML_HEAD % dict(
    title="Station Home",
    tab_home="active", tab_hipot="", tab_dcload="", tab_thermal=""
) + r"""
<main class="app-main">
  <div style="max-width:860px;margin:0 auto;">
    <div class="card" style="text-align:center;padding:32px 24px;">
      <img src="/static/assets/juniper-banner.svg" alt="Juniper" style="max-width:600px;width:100%;margin-bottom:24px;">
      <h1 style="font-size:1.4rem;font-weight:700;color:var(--primary);margin-bottom:8px;">Automated Test Station</h1>
      <p style="color:var(--text-soft);margin-bottom:24px;font-size:.9rem;">Select an instrument below to begin testing.</p>
      <div class="grid-3" style="max-width:600px;margin:0 auto;">
        <a href="/hipot" style="text-decoration:none;">
          <div class="card" style="cursor:pointer;text-align:center;padding:20px;transition:border-color .2s;" onmouseover="this.style.borderColor='var(--primary)'" onmouseout="this.style.borderColor='var(--border)'">
            <div style="font-size:2rem;margin-bottom:8px;">⚡</div>
            <div style="font-weight:700;color:var(--primary);">HiPot</div>
            <div style="font-size:.75rem;color:var(--text-muted);">Vitrek V71</div>
          </div>
        </a>
        <a href="/dcload" style="text-decoration:none;">
          <div class="card" style="cursor:pointer;text-align:center;padding:20px;transition:border-color .2s;" onmouseover="this.style.borderColor='var(--primary)'" onmouseout="this.style.borderColor='var(--border)'">
            <div style="font-size:2rem;margin-bottom:8px;">🔋</div>
            <div style="font-weight:700;color:var(--primary);">DC Load</div>
            <div style="font-size:.75rem;color:var(--text-muted);">SDL1020X-E</div>
          </div>
        </a>
        <a href="/thermal" style="text-decoration:none;">
          <div class="card" style="cursor:pointer;text-align:center;padding:20px;transition:border-color .2s;" onmouseover="this.style.borderColor='var(--primary)'" onmouseout="this.style.borderColor='var(--border)'">
            <div style="font-size:2rem;margin-bottom:8px;">🌡</div>
            <div style="font-weight:700;color:var(--primary);">Thermal Rig</div>
            <div style="font-size:.75rem;color:var(--text-muted);">LOGO! PLC</div>
          </div>
        </a>
      </div>
    </div>
    <div class="card">
      <h2>Recent Test Sessions
        <a href="/api/export" style="float:right;font-size:.76rem;color:var(--primary);text-decoration:none;">⬇ Export All</a>
      </h2>
      <div id="stats-line" style="font-size:.8rem;color:var(--text-muted);margin-bottom:8px;"></div>
      <table class="results-table"><thead><tr>
        <th>#</th><th>Started</th><th>Part / DUT</th><th>Operator</th><th>Result</th><th></th>
      </tr></thead><tbody id="sessions-body"></tbody></table>
    </div>
  </div>
</main>
""" + _HTML_FOOT % dict(extra_js=r"""<script>
(async function(){
  const r=await fetch('/api/sessions?limit=20');const j=await r.json();
  if(!j.ok)return;
  const s=j.stats;
  document.getElementById('stats-line').textContent=`${s.total} sessions  ·  ${s.passed} passed  ·  ${s.failed} failed`;
  const tb=document.getElementById('sessions-body');
  j.sessions.forEach(row=>{
    const p=row.passed;const cls=p===1?'pass':p===0?'fail':'';
    const tag=p===1?'<span class="tag-pass">✓ PASS</span>':p===0?'<span class="tag-fail">✗ FAIL</span>':'—';
    const tr=document.createElement('tr');tr.className=cls;
    tr.innerHTML=`<td>${row.id}</td><td>${(row.started_at||'').slice(0,16)}</td><td>${row.part_number||'—'} / ${row.serial_number||'—'}</td><td>${row.operator||'—'}</td><td>${tag}</td><td><a href="/api/export/${row.id}" style="color:var(--primary);font-size:.75rem;">⬇</a></td>`;
    tb.appendChild(tr);
  });
})();
</script>""")


# ── HiPot page ─────────────────────────────────────────────────────────────────
_HIPOT_HTML = _HTML_HEAD % dict(
    title="HiPot — V71",
    tab_home="", tab_hipot="active", tab_dcload="", tab_thermal=""
) + r"""
<main class="app-main">
  <div class="layout-2col">
    <div class="col-left">
      <div class="card">
        <h2>Connection</h2>
        <label>Interface</label>
        <select id="iface" onchange="document.getElementById('serial-opts').style.display=this.value==='serial'?'':'none'">
          <option value="usb">USB (HID-to-UART)</option>
          <option value="serial">RS-232 / COM Port</option>
        </select>
        <div id="serial-opts" style="display:none">
          <label>COM Port</label><input id="com-port" value="COM1">
          <label>Baud Rate</label>
          <select id="baud"><option value="115200" selected>115200</option><option>57600</option><option>19200</option></select>
        </div>
        <div class="btn-row">
          <button class="btn-primary" id="btn-connect" onclick="doConnect()">Connect</button>
          <button class="btn-muted"   id="btn-disconnect" disabled onclick="doDisconnect()">Disconnect</button>
        </div>
        <div id="idn-info" style="font-size:.76rem;color:var(--text-muted);margin-top:6px;"></div>
      </div>
      <div class="card">
        <h2>Test Parameters</h2>
        <label>Operator</label><input id="operator" placeholder="Name">
        <label>Part Number</label><input id="part-number">
        <label>DUT Serial</label><input id="dut-serial">
        <label>Notes</label><input id="notes">
      </div>
      <div class="card">
        <h2>Sequence Builder</h2>
        <label>Add Step</label>
        <select id="new-step-type">
          <option value="ACW">ACW — AC Withstand</option>
          <option value="DCW">DCW — DC Withstand</option>
          <option value="IR">IR — Insulation Resistance</option>
          <option value="GB">GB — Ground Bond</option>
          <option value="CONT">CONT — Continuity</option>
        </select>
        <button class="btn-muted" onclick="addStep()">+ Add Step</button>
        <div id="steps-list" style="margin-top:8px;"></div>
        <div class="btn-row">
          <button class="btn-green"  id="btn-run"   disabled onclick="doRun()">▶ Run</button>
          <button class="btn-red"    id="btn-abort" disabled onclick="doAbort()">■ Abort</button>
          <button class="btn-orange" id="btn-cont"  disabled onclick="doCont()">▶▶ Cont</button>
        </div>
        <div id="run-msg"></div>
      </div>
    </div>
    <div class="col-right">
      <div class="card">
        <h2>Live Measurements</h2>
        <div class="grid-3">
          <div class="live-tile"><div class="lv-label">Volts (V)</div><div class="lv-val" id="lv-volts">—</div></div>
          <div class="live-tile"><div class="lv-label">Amps (A)</div><div class="lv-val" id="lv-amps">—</div></div>
          <div class="live-tile"><div class="lv-label">Ohms (Ω)</div><div class="lv-val" id="lv-ohms">—</div></div>
        </div>
        <div id="step-progress" style="display:flex;gap:4px;flex-wrap:wrap;margin-top:10px;"></div>
        <div id="run-status" style="font-size:.8rem;color:var(--text-muted);margin-top:4px;">Idle</div>
      </div>
      <div class="card">
        <h2>History <a href="/api/export" style="float:right;font-size:.76rem;color:var(--primary);text-decoration:none;">⬇ All</a></h2>
        <div id="hist-stats" style="font-size:.78rem;color:var(--text-muted);margin-bottom:6px;"></div>
        <table class="results-table"><thead><tr>
          <th>#</th><th>Started</th><th>Part/DUT</th><th>Result</th><th></th>
        </tr></thead><tbody id="hist-body"></tbody></table>
      </div>
    </div>
  </div>
</main>
""" + _HTML_FOOT % dict(extra_js=r"""<script>
let _stepUid=0,_pollTimer=null,_running=false;

function addStep(){
  const type=document.getElementById('new-step-type').value,uid=_stepUid++;
  const list=document.getElementById('steps-list');
  const div=document.createElement('div');div.className='step-item';div.id=`step-${uid}`;div.dataset.type=type;
  div.innerHTML=`<button class="del-step" onclick="removeStep(${uid})">✕</button><h4></h4>${fieldHtml(type,uid)}`;
  list.appendChild(div);renumber();
}
function removeStep(uid){document.getElementById(`step-${uid}`)?.remove();renumber();}
function renumber(){document.querySelectorAll('#steps-list .step-item').forEach((el,i)=>el.querySelector('h4').textContent=`Step ${i+1}: ${el.dataset.type}`);}
function fieldHtml(t,uid){
  const v=`steps[${uid}]`;
  if(t==='ACW')return`<div class="grid-2"><div><label>Voltage (Vrms)</label><input name="${v}.voltage" value="1000"></div><div><label>Ramp (s)</label><input name="${v}.ramp" value="1.5"></div><div><label>Dwell (s)</label><input name="${v}.dwell" value="60"></div><div><label>Max Leakage (A)</label><input name="${v}.max_leakage" value="0.005"></div></div>`;
  if(t==='DCW')return`<div class="grid-2"><div><label>Voltage (V)</label><input name="${v}.voltage" value="1000"></div><div><label>Ramp (s)</label><input name="${v}.ramp" value="1.5"></div><div><label>Dwell (s)</label><input name="${v}.dwell" value="60"></div><div><label>Max Leakage (A)</label><input name="${v}.max_leakage" value="0.000025"></div></div>`;
  if(t==='IR') return`<div class="grid-2"><div><label>Voltage (V)</label><input name="${v}.voltage" value="500"></div><div><label>Dwell (s)</label><input name="${v}.dwell" value="60"></div><div><label>Min Resistance (Ω)</label><input name="${v}.min_resistance" value="100000000"></div><div><label>Precheck delay (s)</label><input name="${v}.precheck_delay" value="0"></div></div>`;
  if(t==='GB') return`<div class="grid-2"><div><label>Current (A)</label><input name="${v}.current" value="25"></div><div><label>Dwell (s)</label><input name="${v}.dwell" value="5"></div><div><label>Max Ω</label><input name="${v}.max_ohm" value="0.1"></div></div>`;
  if(t==='CONT')return`<div class="grid-2"><div><label>Test time (s)</label><input name="${v}.dwell" value="5"></div><div><label>Min Ω</label><input name="${v}.min_ohm" value="1.0"></div><div><label>Max Ω</label><input name="${v}.max_ohm" value="2.0"></div></div>`;
  return'';
}
function collectSteps(){
  const out=[];
  document.querySelectorAll('#steps-list .step-item').forEach(item=>{
    const obj={type:item.dataset.type};
    item.querySelectorAll('input').forEach(i=>{const k=i.name.replace(/^steps\[\d+\]\./,'');if(k)obj[k]=i.value;});
    out.push(obj);
  });
  return out;
}
async function doConnect(){
  const mode=document.getElementById('iface').value;
  const p={instrument:'hipot',mode};
  if(mode==='serial'){p.port=document.getElementById('com-port').value;p.baud=document.getElementById('baud').value;}
  const j=await(await fetch('/api/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)})).json();
  if(j.ok){setConn(true);document.getElementById('idn-info').textContent=`${j.idn.manufacturer} ${j.idn.model}  S/N:${j.idn.serial}  FW:${j.idn.firmware}`;showMsg('run-msg',`Connected to ${j.idn.model}`,'ok');}
  else showMsg('run-msg','Connect failed: '+j.error,'err');
}
async function doDisconnect(){await fetch('/api/disconnect',{method:'POST'});setConn(false);document.getElementById('idn-info').textContent='';}
function setConn(c){
  document.getElementById('btn-connect').disabled=c;
  document.getElementById('btn-disconnect').disabled=!c;
  document.getElementById('btn-run').disabled=!c;
}
async function doRun(){
  const steps=collectSteps();if(!steps.length){showMsg('run-msg','Add at least one step','err');return;}
  const p={operator:document.getElementById('operator').value,part_number:document.getElementById('part-number').value,serial_number:document.getElementById('dut-serial').value,notes:document.getElementById('notes').value,steps};
  const j=await(await fetch('/api/hipot/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)})).json();
  if(j.ok){showMsg('run-msg',`Session #${j.session_id} started`,'ok');document.getElementById('btn-run').disabled=true;document.getElementById('btn-abort').disabled=false;_running=true;startPoll();}
  else showMsg('run-msg','Error: '+j.error,'err');
}
async function doAbort(){await fetch('/api/hipot/abort',{method:'POST'});}
async function doCont(){await fetch('/api/hipot/cont',{method:'POST'});}
function startPoll(){if(_pollTimer)clearInterval(_pollTimer);_pollTimer=setInterval(poll,500);}
async function poll(){
  const j=await(await fetch('/api/hipot/status')).json();
  if(!j.connected){clearInterval(_pollTimer);return;}
  document.getElementById('run-status').textContent=j.running?`Running — step ${j.current_step}`:'Idle';
  document.getElementById('btn-abort').disabled=!j.running;
  document.getElementById('btn-cont').disabled=!j.running;
  document.getElementById('btn-run').disabled=j.running;
  const prog=document.getElementById('step-progress');prog.innerHTML='';
  (j.step_status||'').split('').forEach((ch,i)=>{const b=document.createElement('span');b.className='step-badge '+(ch==='?'?'running':ch);b.textContent=`S${i+1}:${ch==='?'?'…':ch}`;prog.appendChild(b);});
  if(j.running){
    const lr=await(await fetch('/api/hipot/live')).json();
    document.getElementById('lv-volts').textContent=lr.volts!=null?lr.volts.toExponential(3):'—';
    document.getElementById('lv-amps').textContent=lr.amps!=null?lr.amps.toExponential(3):'—';
    document.getElementById('lv-ohms').textContent=lr.ohms!=null?lr.ohms.toExponential(3):'—';
  } else if(_running){_running=false;clearInterval(_pollTimer);loadHistory();}
}
function showMsg(id,txt,type){document.getElementById(id).innerHTML=`<div class="msg ${type}">${txt}</div>`;}
async function loadHistory(){
  const r=await(await fetch('/api/sessions?limit=10')).json();if(!r.ok)return;
  document.getElementById('hist-stats').textContent=`${r.stats.total} total · ${r.stats.passed} pass · ${r.stats.failed} fail`;
  const tb=document.getElementById('hist-body');tb.innerHTML='';
  r.sessions.forEach(row=>{const p=row.passed;const cls=p===1?'pass':p===0?'fail':'';const tag=p===1?'<span class="tag-pass">✓</span>':p===0?'<span class="tag-fail">✗</span>':'—';const tr=document.createElement('tr');tr.className=cls;tr.innerHTML=`<td>${row.id}</td><td>${(row.started_at||'').slice(0,16)}</td><td>${row.part_number||'—'}/${row.serial_number||'—'}</td><td>${tag}</td><td><a href="/api/export/${row.id}" style="color:var(--primary);font-size:.72rem;">⬇</a></td>`;tb.appendChild(tr);});
}
loadHistory();
setInterval(async()=>{if(!_running){const j=await(await fetch('/api/hipot/status')).json();if(j.running){_running=true;startPoll();}}},2000);
</script>""")


# ── DC Load page ───────────────────────────────────────────────────────────────
_DCLOAD_HTML = _HTML_HEAD % dict(
    title="DC Load — SDL1020X",
    tab_home="", tab_hipot="", tab_dcload="active", tab_thermal=""
) + r"""
<main class="app-main">
  <div class="layout-2col">
    <div class="col-left">
      <div class="card">
        <h2>Connection</h2>
        <label>Interface</label>
        <select id="dl-iface" onchange="document.getElementById('dl-serial-opts').style.display=this.value==='serial'?'':'none'">
          <option value="tcp">LAN / TCP (recommended)</option>
          <option value="serial">USB CDC / COM Port</option>
        </select>
        <div>
          <label>Host IP</label><input id="dl-host" value="192.168.1.101">
          <label>Port</label><input id="dl-port" value="5025">
        </div>
        <div id="dl-serial-opts" style="display:none">
          <label>COM Port</label><input id="dl-com" value="COM5">
        </div>
        <div class="btn-row">
          <button class="btn-primary" id="dl-btn-connect" onclick="dlConnect()">Connect</button>
          <button class="btn-muted"   id="dl-btn-disconnect" disabled onclick="dlDisconnect()">Disconnect</button>
        </div>
        <div id="dl-idn" style="font-size:.76rem;color:var(--text-muted);margin-top:6px;"></div>
      </div>
      <div class="card">
        <h2>Load Configuration</h2>
        <label>Mode</label>
        <select id="dl-mode">
          <option value="CC">CC — Constant Current</option>
          <option value="CV">CV — Constant Voltage</option>
          <option value="CR">CR — Constant Resistance</option>
          <option value="CP">CP — Constant Power</option>
        </select>
        <label id="dl-val-label">Set Current (A)</label>
        <input id="dl-value" type="number" step="0.001" value="1.0">
        <div class="btn-row">
          <button class="btn-primary" id="dl-btn-set" disabled onclick="dlConfigure()">Apply</button>
          <button class="btn-green"   id="dl-btn-on"  disabled onclick="dlInput(true)">▶ Input ON</button>
          <button class="btn-red"     id="dl-btn-off" disabled onclick="dlInput(false)">■ Input OFF</button>
        </div>
        <div id="dl-msg"></div>
      </div>
    </div>
    <div class="col-right">
      <div class="card">
        <h2>Live Measurements <span id="dl-input-badge" style="font-size:.75rem;color:var(--text-muted);"></span></h2>
        <div class="grid-2" style="margin-bottom:10px;">
          <div class="live-tile"><div class="lv-label">Voltage (V)</div><div class="lv-val" id="dl-volts">—</div></div>
          <div class="live-tile"><div class="lv-label">Current (A)</div><div class="lv-val" id="dl-amps">—</div></div>
          <div class="live-tile"><div class="lv-label">Power (W)</div><div class="lv-val" id="dl-watts">—</div></div>
          <div class="live-tile"><div class="lv-label">Resistance (Ω)</div><div class="lv-val" id="dl-ohms">—</div></div>
        </div>
        <div id="dl-status-line" style="font-size:.8rem;color:var(--text-muted);">Not connected</div>
      </div>
    </div>
  </div>
</main>
""" + _HTML_FOOT % dict(extra_js=r"""<script>
let dlPoll=null;
document.getElementById('dl-mode').addEventListener('change',e=>{
  const labels={CC:'Set Current (A)',CV:'Set Voltage (V)',CR:'Set Resistance (Ω)',CP:'Set Power (W)'};
  document.getElementById('dl-val-label').textContent=labels[e.target.value]||'Value';
});
async function dlConnect(){
  const mode=document.getElementById('dl-iface').value;
  const p={instrument:'dcload',mode,host:document.getElementById('dl-host').value,port:document.getElementById('dl-port').value,port_serial:document.getElementById('dl-com').value};
  const j=await(await fetch('/api/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)})).json();
  if(j.ok){dlSetConn(true);document.getElementById('dl-idn').textContent=`${j.idn.manufacturer} ${j.idn.model}  S/N:${j.idn.serial}`;startDlPoll();}
  else showMsg('dl-msg','Connect failed: '+j.error,'err');
}
async function dlDisconnect(){await fetch('/api/disconnect',{method:'POST'});dlSetConn(false);if(dlPoll)clearInterval(dlPoll);}
function dlSetConn(c){
  document.getElementById('dl-btn-connect').disabled=c;
  document.getElementById('dl-btn-disconnect').disabled=!c;
  document.getElementById('dl-btn-set').disabled=!c;
  document.getElementById('dl-btn-on').disabled=!c;
  document.getElementById('dl-btn-off').disabled=!c;
}
async function dlConfigure(){
  const p={mode:document.getElementById('dl-mode').value,value:document.getElementById('dl-value').value};
  const j=await(await fetch('/api/dcload/configure',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)})).json();
  if(j.ok)showMsg('dl-msg',`Mode: ${j.mode}  Set: ${j.value}`,'ok');
  else showMsg('dl-msg','Error: '+j.error,'err');
}
async function dlInput(on){
  const j=await(await fetch('/api/dcload/input',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on})})).json();
  if(!j.ok)showMsg('dl-msg','Error: '+j.error,'err');
}
function startDlPoll(){if(dlPoll)clearInterval(dlPoll);dlPoll=setInterval(dlMeasure,500);}
async function dlMeasure(){
  try{
    const j=await(await fetch('/api/dcload/measure')).json();
    if(!j.ok){document.getElementById('dl-status-line').textContent='Disconnected';return;}
    document.getElementById('dl-volts').textContent=j.voltage_v!=null?j.voltage_v.toFixed(4):'—';
    document.getElementById('dl-amps').textContent=j.current_a!=null?j.current_a.toFixed(4):'—';
    document.getElementById('dl-watts').textContent=j.power_w!=null?j.power_w.toFixed(3):'—';
    document.getElementById('dl-ohms').textContent=j.resistance_ohm!=null?j.resistance_ohm.toFixed(3):'—';
    document.getElementById('dl-input-badge').textContent=j.input_on?'● INPUT ON':'○ INPUT OFF';
    document.getElementById('dl-input-badge').style.color=j.input_on?'var(--success)':'var(--text-muted)';
    document.getElementById('dl-status-line').textContent=`Mode: ${j.mode}`;
  }catch(e){document.getElementById('dl-status-line').textContent='Read error';}
}
function showMsg(id,txt,type){document.getElementById(id).innerHTML=`<div class="msg ${type}">${txt}</div>`;}
</script>""")


# ── Thermal rig page ───────────────────────────────────────────────────────────
_THERMAL_HTML = _HTML_HEAD % dict(
    title="Thermal Rig",
    tab_home="", tab_hipot="", tab_dcload="", tab_thermal="active"
) + r"""
<main class="app-main">
  <div class="layout-2col">
    <div class="col-left">
      <div class="card">
        <h2>Connection</h2>
        <label>PLC IP Address</label><input id="plc-ip" value="192.168.1.100">
        <div class="btn-row">
          <button class="btn-primary" id="th-btn-connect" onclick="thConnect()">Connect Rig</button>
          <button class="btn-muted"   id="th-btn-disconnect" disabled onclick="thDisconnect()">Disconnect</button>
        </div>
        <div id="th-conn-info" style="font-size:.76rem;color:var(--text-muted);margin-top:6px;"></div>
      </div>
      <div class="card">
        <h2>Temperature Control</h2>
        <label>Target Temperature (°C)</label>
        <input id="th-setpoint" type="number" value="25" min="0" max="120" step="0.5">
        <div class="btn-row">
          <button class="btn-primary" onclick="thSetpoint()">Set Target</button>
          <button class="btn-green"   id="th-start-pid" disabled onclick="thControl(true)">▶ Start PID</button>
          <button class="btn-red"     id="th-stop-pid"  disabled onclick="thControl(false)">■ Stop PID</button>
        </div>
      </div>
      <div class="card">
        <h2>Vent Valves</h2>
        <label>Intake Vent A (%)</label>
        <input id="vent-a" type="range" min="0" max="100" value="0" oninput="document.getElementById('vent-a-val').textContent=this.value+'%'">
        <span id="vent-a-val" style="font-size:.8rem;color:var(--text-muted);">0%</span>
        <label>Exhaust Vent B (%)</label>
        <input id="vent-b" type="range" min="0" max="100" value="0" oninput="document.getElementById('vent-b-val').textContent=this.value+'%'">
        <span id="vent-b-val" style="font-size:.8rem;color:var(--text-muted);">0%</span>
        <div class="btn-row">
          <button class="btn-primary" id="th-btn-vents" disabled onclick="thVents()">Apply Vents</button>
        </div>
      </div>
      <div class="card">
        <h2>PLC Outputs</h2>
        <div class="io-table" id="plc-outputs"></div>
        <div id="plc-msg"></div>
      </div>
    </div>
    <div class="col-right">
      <div class="card">
        <h2>Thermocouples</h2>
        <div class="temp-grid" id="tc-grid">
          <div class="temp-tile"><div class="tc-label">TC1</div><div class="tc-desc">Ambient / chamber air</div><div class="tc-val" id="tc1">—</div></div>
          <div class="temp-tile"><div class="tc-label">TC2</div><div class="tc-desc">DUT surface</div><div class="tc-val" id="tc2">—</div></div>
          <div class="temp-tile"><div class="tc-label">TC3</div><div class="tc-desc">Heater element</div><div class="tc-val" id="tc3">—</div></div>
          <div class="temp-tile"><div class="tc-label">TC4</div><div class="tc-desc">Exhaust / vent</div><div class="tc-val" id="tc4">—</div></div>
        </div>
      </div>
      <div class="card">
        <h2>Heater Status</h2>
        <div style="margin-bottom:4px;font-size:.8rem;color:var(--text-soft);">Duty Cycle: <span id="heater-duty-pct">—</span></div>
        <div class="gauge-bar"><div class="gauge-fill" id="heater-gauge" style="width:0%"></div></div>
        <div style="margin-top:10px;font-size:.8rem;color:var(--text-muted);">
          Setpoint: <span id="th-setpoint-display">—</span> °C  ·  Control: <span id="th-control-state">inactive</span>
        </div>
        <div id="th-fault" style="font-size:.8rem;color:var(--error-fg);margin-top:4px;"></div>
      </div>
      <div class="card">
        <h2>PLC Inputs</h2>
        <div class="io-table" id="plc-inputs"></div>
      </div>
    </div>
  </div>
</main>
""" + _HTML_FOOT % dict(extra_js=r"""<script>
let thPoll=null;
async function thConnect(){
  const p={instrument:'thermal',plc_host:document.getElementById('plc-ip').value};
  const j=await(await fetch('/api/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)})).json();
  if(j.ok){
    thSetConn(true);
    const w=j.warnings&&j.warnings.length?'  ⚠ '+j.warnings.join(', '):'';
    document.getElementById('th-conn-info').textContent=`PLC ${j.plc_connected?'connected':'offline'}  ·  Thermal ${j.thermal_ready?'ready':'not available'}${w}`;
    startThPoll();
  } else showMsg('th-conn-info',j.error,'err');
}
async function thDisconnect(){await fetch('/api/disconnect',{method:'POST'});thSetConn(false);if(thPoll)clearInterval(thPoll);}
function thSetConn(c){
  document.getElementById('th-btn-connect').disabled=c;
  document.getElementById('th-btn-connect').disabled=c;
  document.getElementById('th-btn-disconnect').disabled=!c;
  document.getElementById('th-start-pid').disabled=!c;
  document.getElementById('th-stop-pid').disabled=!c;
  document.getElementById('th-btn-vents').disabled=!c;
}
async function thSetpoint(){
  const t=parseFloat(document.getElementById('th-setpoint').value);
  await fetch('/api/thermal/setpoint',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({temp_c:t})});
}
async function thControl(start){
  await fetch('/api/thermal/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start})});
}
async function thVents(){
  await fetch('/api/thermal/vent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({vent:'A',percent:parseFloat(document.getElementById('vent-a').value)})});
  await fetch('/api/thermal/vent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({vent:'B',percent:parseFloat(document.getElementById('vent-b').value)})});
}
function startThPoll(){if(thPoll)clearInterval(thPoll);thPoll=setInterval(thUpdate,1000);}
async function thUpdate(){
  try{
    const j=await(await fetch('/api/thermal/status')).json();
    if(!j.ok)return;
    // Temps
    const tm=j.temps||{};
    const keys=['TC1_AMBIENT','TC2_DUT','TC3_HEATER','TC4_EXHAUST'];
    const ids=['tc1','tc2','tc3','tc4'];
    keys.forEach((k,i)=>{
      const v=tm[k];const el=document.getElementById(ids[i]);
      if(v==null){el.textContent='—';el.className='tc-val';}
      else{el.textContent=v.toFixed(1)+'°C';el.className='tc-val'+(v>100?' alarm':v>60?' hot':'');}
    });
    // Heater
    const duty=j.heater_duty_pct||0;
    document.getElementById('heater-duty-pct').textContent=duty.toFixed(1)+'%';
    document.getElementById('heater-gauge').style.width=duty+'%';
    document.getElementById('heater-gauge').style.background=duty>75?'var(--error-fg)':duty>40?'#c46200':'var(--primary)';
    document.getElementById('th-setpoint-display').textContent=(j.setpoint_c||25).toFixed(1);
    document.getElementById('th-control-state').textContent=j.control_active?'ACTIVE':'inactive';
    document.getElementById('th-fault').textContent=j.fault||'';
    // PLC I/O
    if(j.plc_inputs){renderIO('plc-inputs',j.plc_inputs,false);}
    if(j.plc_safe!==undefined){
      const pill=document.createElement('div');
    }
  }catch(e){}
  // PLC outputs
  try{
    const io=await(await fetch('/api/plc/io')).json();
    if(io.ok){renderIO('plc-outputs',io.outputs,true);renderIO('plc-inputs',io.inputs,false);}
  }catch(e){}
}
function renderIO(elemId,data,isOutput){
  const container=document.getElementById(elemId);if(!container)return;
  container.innerHTML='';
  Object.entries(data).forEach(([name,state])=>{
    const row=document.createElement('div');row.className='io-row';
    const led=`<span class="io-led ${state?'on':'off'}"></span>`;
    const btn=isOutput?`<button class="btn-muted" style="padding:2px 8px;font-size:.72rem;margin-top:0;" onclick="toggleOutput('${name}',${!state})">${state?'OFF':'ON'}</button>`:'';
    row.innerHTML=`${led}<span style="flex:1;margin-left:6px;">${name}</span>${btn}`;
    container.appendChild(row);
  });
}
async function toggleOutput(name,state){
  await fetch('/api/plc/output',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,state})});
}
function showMsg(id,txt,type){document.getElementById(id).innerHTML=`<div class="msg ${type}">${txt}</div>`;}
</script>""")


# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    db.init_db()
    print("Juniper Test Station starting at http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
