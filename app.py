"""
app.py
------
Flask web app for controlling the Vitrek V71 HiPot Tester.

Endpoints:
  GET  /                    - Main UI
  POST /api/connect         - Connect to V71 (USB or serial)
  POST /api/disconnect      - Disconnect
  GET  /api/status          - Device / run status
  POST /api/run             - Configure sequence + start test
  POST /api/abort           - Abort running test
  POST /api/cont            - Continue from HOLD
  GET  /api/live            - Live measurement values (poll during run)
  GET  /api/sessions        - List recent test sessions
  GET  /api/session/<id>    - Session detail + step results
  GET  /api/export          - Download Excel file for all sessions
  GET  /api/export/<id>     - Download Excel file for one session

Run:  python app.py
"""

import os
import threading
import time
import datetime
import tempfile
from flask import Flask, jsonify, request, send_file, render_template_string

import database as db
from excel_export import export_to_excel

# Conditionally import driver — won't crash on Linux dev machines
try:
    from v71_driver import V71Driver, V71Error
    DRIVER_AVAILABLE = True
except Exception:
    V71Driver = None
    V71Error = Exception
    DRIVER_AVAILABLE = False

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# ---------------------------------------------------------------------------
# Global state (single-device, single-session at a time)
# ---------------------------------------------------------------------------
driver: "V71Driver | None" = None
driver_lock = threading.Lock()
current_session_id: int | None = None
current_step_types: list[str] = []   # ordered list of step types for result labeling
_run_thread: threading.Thread | None = None


def _driver_or_error():
    if driver is None or not driver.connected:
        return None, jsonify({"ok": False, "error": "Not connected to V71"}), 400
    return driver, None, None


# ---------------------------------------------------------------------------
# Background run monitor
# ---------------------------------------------------------------------------
def _monitor_run(session_id: int, step_types: list[str]):
    """Poll the V71 until the test finishes, then save all results."""
    global current_session_id
    try:
        # Wait for run to start
        time.sleep(0.5)
        while driver and driver.connected:
            if not driver.is_running():
                break
            time.sleep(0.25)

        if not driver or not driver.connected:
            return

        # Collect results
        overall = driver.overall_result()
        db.finish_session(session_id, overall)

        stat = driver.step_status_string()
        for i, ch in enumerate(stat, start=1):
            step_type = step_types[i - 1] if i <= len(step_types) else "UNKNOWN"
            result = driver.step_result(i)
            db.save_step_result(session_id, i, step_type, result)

    except Exception as e:
        app.logger.error(f"Monitor thread error: {e}")
    finally:
        current_session_id = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(HTML_UI)


@app.route("/api/connect", methods=["POST"])
def connect():
    global driver
    data = request.get_json(force=True)
    mode = data.get("mode", "usb")  # "usb" or "serial"
    port = data.get("port", "COM1")
    baud = int(data.get("baud", 115200))

    with driver_lock:
        if driver and driver.connected:
            driver.disconnect()
        if not DRIVER_AVAILABLE:
            return jsonify({"ok": False, "error": "v71_driver not available (check DLL path)"}), 500
        try:
            driver = V71Driver()
            if mode == "usb":
                driver.connect_usb()
            else:
                driver.connect_serial(port, baud)
            idn = driver.identify()
            db.init_db()
            return jsonify({"ok": True, "idn": idn})
        except Exception as e:
            driver = None
            return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/disconnect", methods=["POST"])
def disconnect():
    global driver
    with driver_lock:
        if driver:
            driver.disconnect()
            driver = None
    return jsonify({"ok": True})


@app.route("/api/status")
def status():
    if not driver or not driver.connected:
        return jsonify({"connected": False})
    try:
        running = driver.is_running()
        step = driver.current_step() if running else 0
        stat_str = driver.step_status_string() if running else ""
        return jsonify({
            "connected": True,
            "running": running,
            "current_step": step,
            "step_status": stat_str,
            "session_id": current_session_id,
        })
    except Exception as e:
        return jsonify({"connected": False, "error": str(e)})


@app.route("/api/live")
def live():
    if not driver or not driver.connected or not driver.is_running():
        return jsonify({"ok": False})
    results = {}
    for qty in ("AMPS", "VOLTS", "OHMS"):
        try:
            results[qty.lower()] = driver.live_measurement(qty)
        except Exception:
            results[qty.lower()] = None
    return jsonify({"ok": True, **results})


@app.route("/api/run", methods=["POST"])
def run_test():
    global driver, current_session_id, current_step_types, _run_thread
    d = request.get_json(force=True)

    if not driver or not driver.connected:
        return jsonify({"ok": False, "error": "Not connected"}), 400

    # Meta
    operator      = d.get("operator", "")
    part_number   = d.get("part_number", "")
    serial_number = d.get("serial_number", "")
    notes         = d.get("notes", "")
    steps         = d.get("steps", [])   # list of step config dicts

    if not steps:
        return jsonify({"ok": False, "error": "No test steps provided"}), 400

    try:
        idn = driver.identify()
        driver.new_sequence()

        step_types = []
        for step in steps:
            st = step.get("type", "").upper()
            step_types.append(st)
            if st == "ACW":
                driver.add_acw_step(
                    voltage_v=float(step["voltage"]),
                    ramp_s=float(step.get("ramp", 1.5)),
                    dwell_s=float(step.get("dwell", 60)),
                    max_leakage_a=float(step.get("max_leakage", 0.005)),
                    min_leakage_a=float(step["min_leakage"]) if step.get("min_leakage") else None,
                    grounded=bool(step.get("grounded", False)),
                )
            elif st == "DCW":
                driver.add_dcw_step(
                    voltage_v=float(step["voltage"]),
                    ramp_s=float(step.get("ramp", 1.5)),
                    dwell_s=float(step.get("dwell", 60)),
                    max_leakage_a=float(step.get("max_leakage", 25e-6)),
                    min_leakage_a=float(step["min_leakage"]) if step.get("min_leakage") else None,
                    grounded=bool(step.get("grounded", False)),
                    capacitive=bool(step.get("capacitive", False)),
                )
            elif st == "IR":
                driver.add_ir_step(
                    voltage_v=float(step["voltage"]),
                    dwell_s=float(step.get("dwell", 60)),
                    min_resistance_ohm=float(step.get("min_resistance", 100e6)),
                    max_resistance_ohm=float(step["max_resistance"]) if step.get("max_resistance") else None,
                    precheck_delay_s=float(step.get("precheck_delay", 0)),
                    grounded=bool(step.get("grounded", False)),
                )
            elif st == "GB":
                driver.add_gb_step(
                    current_a=float(step["current"]),
                    dwell_s=float(step.get("dwell", 5)),
                    max_ohm=float(step.get("max_ohm", 0.1)),
                    min_ohm=float(step["min_ohm"]) if step.get("min_ohm") else None,
                )
            elif st == "CONT":
                driver.add_cont_step(
                    test_time_s=float(step.get("dwell", 5)),
                    min_ohm=float(step["min_ohm"]) if step.get("min_ohm") else None,
                    max_ohm=float(step["max_ohm"]) if step.get("max_ohm") else None,
                )
            else:
                return jsonify({"ok": False, "error": f"Unknown step type: {st}"}), 400

        session_id = db.create_session(
            operator=operator, part_number=part_number,
            serial_number=serial_number, notes=notes,
            device_model=idn.get("model", ""),
            device_serial=idn.get("serial", ""),
            firmware=idn.get("firmware", ""),
        )
        current_session_id = session_id
        current_step_types = step_types

        driver.run()

        _run_thread = threading.Thread(
            target=_monitor_run, args=(session_id, step_types), daemon=True
        )
        _run_thread.start()

        return jsonify({"ok": True, "session_id": session_id})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/abort", methods=["POST"])
def abort():
    if not driver or not driver.connected:
        return jsonify({"ok": False, "error": "Not connected"}), 400
    try:
        driver.abort()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cont", methods=["POST"])
def cont():
    if not driver or not driver.connected:
        return jsonify({"ok": False, "error": "Not connected"}), 400
    try:
        driver.cont()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sessions")
def sessions():
    db.init_db()
    limit = int(request.args.get("limit", 50))
    rows = db.get_sessions(limit=limit)
    stats = db.get_stats()
    return jsonify({"ok": True, "sessions": rows, "stats": stats})


@app.route("/api/session/<int:session_id>")
def session_detail(session_id):
    session = db.get_session(session_id)
    if not session:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    steps = db.get_steps(session_id)
    return jsonify({"ok": True, "session": session, "steps": steps})


@app.route("/api/export")
def export_all():
    db.init_db()
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    export_to_excel(tmp.name)
    fname = f"hipot_results_{datetime.date.today()}.xlsx"
    return send_file(tmp.name, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/export/<int:session_id>")
def export_session(session_id):
    db.init_db()
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    export_to_excel(tmp.name, session_ids=[session_id])
    fname = f"hipot_session_{session_id}_{datetime.date.today()}.xlsx"
    return send_file(tmp.name, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# Embedded single-file UI (Juniper branded)
# ---------------------------------------------------------------------------
HTML_UI = r"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V71 HiPot Controller — Juniper Design</title>
<link rel="icon" type="image/svg+xml" href="/static/assets/juniper-logo.svg">
<link rel="stylesheet" href="/static/css/juniper-brand.css">
<style>
  /* ── Layout ── */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    background: var(--bg);
    color: var(--text);
    font-family: var(--juniper-font);
  }
  .app-main {
    flex: 1;
    display: grid;
    grid-template-columns: 330px 1fr;
    gap: 16px;
    padding: 16px;
    max-width: 1440px;
    margin: 0 auto;
    width: 100%;
  }
  /* ── Cards ── */
  .card {
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    box-shadow: 0 2px 8px var(--shadow);
  }
  .card h2 {
    font-size: .9rem;
    font-weight: 600;
    color: var(--primary);
    margin-bottom: 12px;
    border-bottom: 2px solid var(--border);
    padding-bottom: 6px;
    letter-spacing: .04em;
    text-transform: uppercase;
  }
  /* ── Forms ── */
  label { display: block; font-size: .8rem; font-weight: 600; margin-top: 8px; margin-bottom: 2px; color: var(--text-soft); }
  input, select {
    width: 100%;
    padding: 6px 9px;
    border: 1px solid var(--input-border);
    border-radius: 5px;
    font-size: .88rem;
    background: var(--input-bg);
    color: var(--text);
    font-family: var(--juniper-font);
  }
  input:focus, select:focus { outline: 2px solid var(--primary); outline-offset: 1px; }
  /* ── Buttons ── */
  button {
    padding: 7px 14px;
    border: none;
    border-radius: 5px;
    cursor: pointer;
    font-size: .88rem;
    font-weight: 600;
    margin-top: 6px;
    font-family: var(--juniper-font);
    transition: opacity .15s;
  }
  button:disabled { opacity: .45; cursor: default; }
  .btn-primary  { background: var(--primary);  color: #fff; }
  .btn-primary:hover:not(:disabled)  { background: var(--primary-hover); }
  .btn-green    { background: var(--success);  color: #fff; }
  .btn-green:hover:not(:disabled)    { background: var(--success-hover); }
  .btn-red      { background: var(--error-fg); color: #fff; }
  .btn-orange   { background: #c46200;         color: #fff; }
  .btn-muted    { background: var(--bg-elev-2); color: var(--text-soft); border: 1px solid var(--border); }
  /* ── Connection status pill ── */
  #conn-status {
    font-size: .75rem;
    font-weight: 600;
    padding: 3px 11px;
    border-radius: 12px;
    background: rgba(255,255,255,.15);
    color: var(--juniper-offwhite);
    letter-spacing: .04em;
    text-transform: uppercase;
  }
  #conn-status.connected { background: rgba(46,160,71,.7); }
  /* ── Step builder ── */
  .steps-list { margin-top: 8px; }
  .step-item {
    background: var(--bg-elev-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px;
    margin-bottom: 8px;
    position: relative;
  }
  .step-item h4 { font-size: .82rem; color: var(--primary); margin-bottom: 6px; font-weight: 600; }
  .step-item .del-step {
    position: absolute; top: 6px; right: 8px;
    background: none; border: none; color: var(--error-fg);
    font-size: 1rem; cursor: pointer; padding: 0;
  }
  .step-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
  /* ── Live measurements ── */
  .live-panel { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-bottom: 12px; }
  .live-val {
    background: var(--bg-elev-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    text-align: center;
  }
  .live-val .lv-label { font-size: .72rem; color: var(--text-muted); font-weight: 600; letter-spacing: .06em; text-transform: uppercase; }
  .live-val .lv-val   { font-size: 1.4rem; font-weight: 700; color: var(--primary); margin-top: 2px; }
  /* ── Step badges ── */
  #step-progress { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 8px; }
  .step-badge {
    padding: 3px 8px;
    border-radius: 4px;
    font-size: .78rem;
    font-weight: 700;
    background: var(--bg-elev-2);
    color: var(--text-muted);
    border: 1px solid var(--border);
  }
  .step-badge.P { background: #d4edda; color: #155724; border-color: #c3e6cb; }
  .step-badge.F { background: var(--error-bg); color: var(--error-fg); border-color: var(--error-border); }
  .step-badge.running { background: var(--warning-bg); color: var(--warning-fg); border-color: var(--warning-border); }
  /* ── Results table ── */
  #results-table { width: 100%; border-collapse: collapse; font-size: .83rem; }
  #results-table th {
    background: var(--table-head-bg);
    color: var(--text-soft);
    padding: 7px 10px;
    text-align: left;
    font-weight: 600;
    font-size: .78rem;
    text-transform: uppercase;
    letter-spacing: .04em;
    border-bottom: 2px solid var(--border);
  }
  #results-table td { padding: 6px 10px; border-bottom: 1px solid var(--hairline); color: var(--text); }
  #results-table tr.pass td { background: rgba(46,127,58,.08); }
  #results-table tr.fail td { background: rgba(218,54,51,.07); }
  /* ── Feedback messages ── */
  .msg { padding: 8px 12px; border-radius: 5px; margin-top: 8px; font-size: .83rem; border: 1px solid transparent; }
  .msg.ok  { background: var(--success-hover); color: #fff; border-color: transparent; }
  .msg.err { background: var(--error-bg); color: var(--error-fg); border-color: var(--error-border); }
  .tag-pass { color: var(--success); font-weight: 700; }
  .tag-fail { color: var(--error-fg); font-weight: 700; }
  /* ── Misc ── */
  .right-panel { display: flex; flex-direction: column; gap: 16px; }
  .left-panel  { display: flex; flex-direction: column; gap: 16px; }
  .btn-row { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
  #run-status-line { font-size: .83rem; color: var(--text-muted); }
  .section-export-link { float: right; font-size: .78rem; color: var(--primary); text-decoration: none; }
  .section-export-link:hover { text-decoration: underline; }
</style>
</head>
<body>

<!-- Juniper brand bar -->
<header class="juniper-brand-bar">
  <div class="juniper-brand-bar-inner">
    <img class="juniper-logo" src="/static/assets/juniper-logo.svg" alt="Juniper Design">
    <div style="display:flex;align-items:center;gap:14px;">
      <span class="juniper-product">V71 HiPot Controller</span>
      <span id="conn-status">Disconnected</span>
      <button id="juniper-theme-toggle" class="juniper-theme-toggle" type="button"
              aria-label="Toggle light/dark theme" title="Toggle theme">
        <span class="juniper-theme-icon">🌙</span>
      </button>
    </div>
  </div>
</header>
<main class="app-main">
  <!-- Left panel: connect + sequence builder -->
  <div class="left-panel">

    <div class="card" id="card-connect">
      <h2>Connection</h2>
      <label>Interface</label>
      <select id="iface">
        <option value="usb">USB (HID-to-UART)</option>
        <option value="serial">RS-232 / COM Port</option>
      </select>
      <div id="serial-opts" style="display:none;">
        <label>COM Port</label>
        <input id="com-port" value="COM3">
        <label>Baud Rate</label>
        <select id="baud">
          <option value="115200" selected>115200</option>
          <option value="57600">57600</option>
          <option value="19200">19200</option>
          <option value="9600">9600</option>
        </select>
      </div>
      <button class="btn-primary" id="btn-connect" onclick="doConnect()">Connect</button>
      <button class="btn-muted" id="btn-disconnect" disabled onclick="doDisconnect()">Disconnect</button>
      <div id="idn-info" style="font-size:.8rem;color:var(--text-muted);margin-top:8px;"></div>
    </div>

    <div class="card">
      <h2>Test Parameters</h2>
      <label>Operator</label>
      <input id="operator" placeholder="Name">
      <label>Part Number</label>
      <input id="part-number">
      <label>DUT Serial Number</label>
      <input id="dut-serial">
      <label>Notes</label>
      <input id="notes">
    </div>

    <div class="card">
      <h2>Test Sequence Builder</h2>
      <label>Add Step</label>
      <select id="new-step-type">
        <option value="ACW">ACW — AC Withstand</option>
        <option value="DCW">DCW — DC Withstand</option>
        <option value="IR">IR — Insulation Resistance</option>
        <option value="GB">GB — Ground Bond</option>
        <option value="CONT">CONT — Continuity</option>
      </select>
      <button class="btn-muted" onclick="addStep()">+ Add Step</button>
      <div class="steps-list" id="steps-list"></div>
      <div class="btn-row">
        <button class="btn-green" id="btn-run" disabled onclick="doRun()">▶ Run Test</button>
        <button class="btn-red" id="btn-abort" disabled onclick="doAbort()">■ Abort</button>
        <button class="btn-orange" id="btn-cont" disabled onclick="doCont()">▶▶ Continue</button>
      </div>
      <div id="run-msg"></div>
    </div>
  </div>

  <!-- Right panel: live + results -->
  <div class="right-panel">

    <div class="card">
      <h2>Live Measurements</h2>
      <div class="live-panel">
        <div class="live-val"><div class="lv-label">Volts (V)</div><div class="lv-val" id="live-volts">—</div></div>
        <div class="live-val"><div class="lv-label">Amps (A)</div><div class="lv-val" id="live-amps">—</div></div>
        <div class="live-val"><div class="lv-label">Ohms (Ω)</div><div class="lv-val" id="live-ohms">—</div></div>
      </div>
      <div id="step-progress"></div>
      <div id="run-status-line">Not running</div>
    </div>

    <div class="card">
      <h2>Test History
        <a href="/api/export" class="section-export-link">⬇ Export All to Excel</a>
      </h2>
      <div id="stats-line" style="font-size:.83rem;margin-bottom:8px;color:var(--text-muted);"></div>
      <table id="results-table">
        <thead>
          <tr>
            <th>#</th><th>Started</th><th>Part / DUT Serial</th>
            <th>Operator</th><th>Result</th><th>Actions</th>
          </tr>
        </thead>
        <tbody id="results-body"></tbody>
      </table>
    </div>
  </div>
</main>

<footer class="juniper-footer">
  Designed and built by <a href="https://juniperdesign.com" target="_blank" rel="noopener">Juniper Design</a>
</footer>

<script src="/static/js/juniper-theme.js"></script>
<script>
let steps = [];
let pollTimer = null;
let running = false;

document.getElementById("iface").addEventListener("change", e => {
  document.getElementById("serial-opts").style.display = e.target.value === "serial" ? "" : "none";
});

function stepDefaults(type) {
  const d = {
    ACW:  {voltage:1000, ramp:1.5, dwell:60, max_leakage:0.005},
    DCW:  {voltage:1000, ramp:1.5, dwell:60, max_leakage:0.000025},
    IR:   {voltage:500,  dwell:60, min_resistance:100000000},
    GB:   {current:25,   dwell:5,  max_ohm:0.1},
    CONT: {dwell:5, min_ohm:1.0, max_ohm:2.0},
  };
  return d[type] || {};
}

function fieldHtml(type, idx) {
  const v = `steps[${idx}]`;
  if (type === "ACW") return `
    <div class="step-grid">
      <div><label>Voltage (Vrms)</label><input name="${v}.voltage" value="1000"></div>
      <div><label>Ramp (s)</label><input name="${v}.ramp" value="1.5"></div>
      <div><label>Dwell (s)</label><input name="${v}.dwell" value="60"></div>
      <div><label>Max Leakage (A)</label><input name="${v}.max_leakage" value="0.005"></div>
    </div>`;
  if (type === "DCW") return `
    <div class="step-grid">
      <div><label>Voltage (V)</label><input name="${v}.voltage" value="1000"></div>
      <div><label>Ramp (s)</label><input name="${v}.ramp" value="1.5"></div>
      <div><label>Dwell (s)</label><input name="${v}.dwell" value="60"></div>
      <div><label>Max Leakage (A)</label><input name="${v}.max_leakage" value="0.000025"></div>
    </div>`;
  if (type === "IR") return `
    <div class="step-grid">
      <div><label>Voltage (V)</label><input name="${v}.voltage" value="500"></div>
      <div><label>Dwell (s)</label><input name="${v}.dwell" value="60"></div>
      <div><label>Min Resistance (Ω)</label><input name="${v}.min_resistance" value="100000000"></div>
      <div><label>Precheck Delay (s)</label><input name="${v}.precheck_delay" value="0"></div>
    </div>`;
  if (type === "GB") return `
    <div class="step-grid">
      <div><label>Current (A)</label><input name="${v}.current" value="25"></div>
      <div><label>Dwell (s)</label><input name="${v}.dwell" value="5"></div>
      <div><label>Max Impedance (Ω)</label><input name="${v}.max_ohm" value="0.1"></div>
    </div>`;
  if (type === "CONT") return `
    <div class="step-grid">
      <div><label>Test Time (s)</label><input name="${v}.dwell" value="5"></div>
      <div><label>Min Ohm</label><input name="${v}.min_ohm" value="1.0"></div>
      <div><label>Max Ohm</label><input name="${v}.max_ohm" value="2.0"></div>
    </div>`;
  return "";
}

function addStep() {
  const type = document.getElementById("new-step-type").value;
  const idx = steps.length;
  steps.push({ type });
  const list = document.getElementById("steps-list");
  const div = document.createElement("div");
  div.className = "step-item";
  div.id = `step-${idx}`;
  div.innerHTML = `
    <button class="del-step" onclick="removeStep(${idx})">✕</button>
    <h4>Step ${idx + 1}: ${type}</h4>
    ${fieldHtml(type, idx)}`;
  list.appendChild(div);
}

function removeStep(idx) {
  document.getElementById(`step-${idx}`)?.remove();
  steps[idx] = null;
}

function collectSteps() {
  const items = document.querySelectorAll(".step-item");
  const out = [];
  items.forEach(item => {
    const inputs = item.querySelectorAll("input");
    const stepObj = { type: item.querySelector("h4").textContent.split(": ")[1] };
    inputs.forEach(inp => {
      const key = inp.name.replace(/^steps\[\d+\]\./, "");
      stepObj[key] = inp.value;
    });
    out.push(stepObj);
  });
  return out;
}

async function doConnect() {
  const mode = document.getElementById("iface").value;
  const payload = { mode };
  if (mode === "serial") {
    payload.port = document.getElementById("com-port").value;
    payload.baud = document.getElementById("baud").value;
  }
  const r = await fetch("/api/connect", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
  const j = await r.json();
  if (j.ok) {
    setConnected(true);
    document.getElementById("idn-info").textContent =
      `${j.idn.manufacturer} ${j.idn.model}  S/N: ${j.idn.serial}  FW: ${j.idn.firmware}`;
    showMsg("run-msg", `Connected to ${j.idn.manufacturer} ${j.idn.model}`, "ok");
  } else {
    showMsg("run-msg", "Connect failed: " + j.error, "err");
  }
}

async function doDisconnect() {
  await fetch("/api/disconnect", { method:"POST" });
  setConnected(false);
  document.getElementById("idn-info").textContent = "";
}

function setConnected(c) {
  const s = document.getElementById("conn-status");
  s.textContent = c ? "Connected" : "Disconnected";
  s.className = c ? "connected" : "";
  document.getElementById("btn-connect").disabled = c;
  document.getElementById("btn-disconnect").disabled = !c;
  document.getElementById("btn-run").disabled = !c;
}

async function doRun() {
  const stepList = collectSteps();
  if (!stepList.length) { showMsg("run-msg", "Add at least one test step", "err"); return; }
  const payload = {
    operator:      document.getElementById("operator").value,
    part_number:   document.getElementById("part-number").value,
    serial_number: document.getElementById("dut-serial").value,
    notes:         document.getElementById("notes").value,
    steps:         stepList,
  };
  const r = await fetch("/api/run", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
  const j = await r.json();
  if (j.ok) {
    showMsg("run-msg", `Test started — session #${j.session_id}`, "ok");
    document.getElementById("btn-run").disabled = true;
    document.getElementById("btn-abort").disabled = false;
    running = true;
    startPoll();
  } else {
    showMsg("run-msg", "Error: " + j.error, "err");
  }
}

async function doAbort() {
  await fetch("/api/abort", { method:"POST" });
  showMsg("run-msg", "Abort sent", "err");
}
async function doCont() {
  await fetch("/api/cont", { method:"POST" });
}

function startPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollStatus, 500);
}

async function pollStatus() {
  const r = await fetch("/api/status");
  const j = await r.json();
  if (!j.connected) { clearInterval(pollTimer); return; }
  document.getElementById("run-status-line").textContent =
    j.running ? `Running — step ${j.current_step}` : "Idle";
  document.getElementById("btn-abort").disabled = !j.running;
  document.getElementById("btn-cont").disabled = !j.running;
  document.getElementById("btn-run").disabled = j.running;

  // Step badges
  const prog = document.getElementById("step-progress");
  prog.innerHTML = "";
  (j.step_status || "").split("").forEach((ch, i) => {
    const b = document.createElement("span");
    b.className = "step-badge " + (ch === "?" ? "running" : ch);
    b.textContent = `S${i+1}:${ch === "?" ? "…" : ch}`;
    prog.appendChild(b);
  });

  if (j.running) {
    const lr = await fetch("/api/live");
    const lj = await lr.json();
    document.getElementById("live-volts").textContent = lj.volts != null ? lj.volts.toExponential(3) : "—";
    document.getElementById("live-amps").textContent  = lj.amps  != null ? lj.amps.toExponential(3)  : "—";
    document.getElementById("live-ohms").textContent  = lj.ohms  != null ? lj.ohms.toExponential(3)  : "—";
  } else if (running) {
    running = false;
    clearInterval(pollTimer);
    loadSessions();
  }
}

async function loadSessions() {
  const r = await fetch("/api/sessions?limit=20");
  const j = await r.json();
  if (!j.ok) return;
  const s = j.stats;
  document.getElementById("stats-line").textContent =
    `${s.total} total  |  ${s.passed} passed  |  ${s.failed} failed`;
  const tbody = document.getElementById("results-body");
  tbody.innerHTML = "";
  j.sessions.forEach(row => {
    const passed = row.passed;
    const cls = passed === 1 ? "pass" : (passed === 0 ? "fail" : "");
    const tag = passed === 1 ? `<span class="tag-pass">✓ PASS</span>` :
                passed === 0 ? `<span class="tag-fail">✗ FAIL</span>` : "—";
    const tr = document.createElement("tr");
    tr.className = cls;
    tr.innerHTML = `
      <td>${row.id}</td>
      <td>${(row.started_at||"").slice(0,19)}</td>
      <td>${row.part_number||"—"} / ${row.serial_number||"—"}</td>
      <td>${row.operator||"—"}</td>
      <td>${tag}</td>
      <td><a href="/api/export/${row.id}" style="color:var(--primary);font-size:.8rem;">⬇ xlsx</a></td>`;
    tbody.appendChild(tr);
  });
}

function showMsg(id, text, type) {
  const el = document.getElementById(id);
  el.innerHTML = `<div class="msg ${type}">${text}</div>`;
}

// Load history on page load
loadSessions();
// Poll status every 2s even when not running (to pick up any external changes)
setInterval(async () => {
  if (!running) { const r = await fetch("/api/status"); const j = await r.json(); if(j.running){running=true;startPoll();} }
}, 2000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    db.init_db()
    print("V71 HiPot Controller starting at http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
