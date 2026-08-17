"""
v71_driver.py
-------------
Low-level communication driver for the Vitrek V71 HiPot Tester.

Supports two transport backends:
  1. USB (HID-to-UART) via Silicon Labs SLABHIDtoUART.dll  (Windows only)
  2. RS-232 / virtual COM port via pyserial  (cross-platform fallback)

USB VID/PID for all V7X units: VID=4292 (0x10C4), PID=34869 (0x8835)
USB UART settings: 115200 8N1, RTS/CTS flow control

Protocol:
  - Commands are ASCII strings terminated with <CR><LF> (\r\n)
  - Responses are terminated with <CR><LF>
  - Multiple commands in one transmission are separated by semicolons
  - Field separator inside a command is a comma

References: V7X Series Operating Manual, Section 6
"""

import ctypes
import os
import time
import threading
from typing import Optional

# ---------------------------------------------------------------------------
# SLABHIDtoUART DLL constants (from SLABCP2110.h)
# ---------------------------------------------------------------------------
HID_UART_SUCCESS = 0x00
HID_UART_EIGHT_DATA_BITS = 0x03
HID_UART_NO_PARITY = 0x00
HID_UART_SHORT_STOP_BIT = 0x00
HID_UART_RTS_CTS_FLOW_CONTROL = 0x01

V7X_VID = 4292   # 0x10C4 – Silicon Labs
V7X_PID = 34869  # 0x8835 – registered unique to V7X

# Path to the DLLs (x64) – resolved relative to this file
_HERE = os.path.dirname(os.path.abspath(__file__))
_DLL_DIR = os.path.join(
    _HERE,
    "software", "drivers", "USB_DLLs_and_Headers",
    "USB DLLs and Headers", "x64"
)
_SLAB_HID_DLL = os.path.join(_DLL_DIR, "SLABHIDtoUART.dll")
_SLAB_DEV_DLL = os.path.join(_DLL_DIR, "SLABHIDDevice.dll")


# Characters the V7X front panel can display in a HOLD message (see the
# "Entry of a name or message" note in Section 4 of the operating manual).
_V7X_MESSAGE_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ#%&:()*+-./<=>@0123456789 ")


def sanitize_message(text: str, max_len: int = 20) -> str:
    """Coerce arbitrary text into something the V7X front panel can display."""
    out = "".join(c for c in (text or "").upper() if c in _V7X_MESSAGE_CHARS)
    return out.strip()[:max_len]


# Test modes each V7X model can actually perform. A verification profile point
# whose mode is not in this list is skipped rather than rejected by the
# instrument with an ERR 2 ("step type not supported by this instrument model").
MODEL_CAPABILITIES = {
    "V70": ("ACW", "CONT"),
    "V71": ("ACW", "DCW", "CONT"),
    "V73": ("ACW", "DCW", "IR", "CONT"),
    "V74": ("ACW", "DCW", "IR", "CONT", "GB"),
    "V75": ("ACW", "DCW", "IR", "CONT"),
    "V76": ("ACW", "DCW", "IR", "CONT"),
    "V77": ("ACW", "DCW", "IR", "CONT", "GB"),
    "V79": ("CONT", "GB"),
}


def capabilities_for_model(model: str) -> tuple:
    """
    Return the tuple of supported test modes for a model string from *IDN?.

    Falls back to the most conservative common set if the model is unknown, so
    an unrecognised instrument never has an unsupported step pushed at it.
    """
    key = (model or "").strip().upper()
    for name, caps in MODEL_CAPABILITIES.items():
        if name in key:
            return caps
    return ("ACW", "CONT")


class V71Error(Exception):
    """Raised when the V71 returns an error or communication fails."""


class V71Driver:
    """
    Thread-safe driver for the Vitrek V71 HiPot Tester.

    Usage (USB):
        driver = V71Driver()
        driver.connect_usb()
        print(driver.identify())
        driver.disconnect()

    Usage (RS-232):
        driver = V71Driver()
        driver.connect_serial("COM3", baud=115200)
        print(driver.identify())
        driver.disconnect()
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._mode: Optional[str] = None   # "usb" or "serial"
        self._hid_handle = None            # ctypes pointer for USB mode
        self._serial = None                # serial.Serial for RS-232 mode
        self._dll = None                   # loaded SLABHIDtoUART DLL

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect_usb(self, device_index: int = 0) -> None:
        """Open the first (or nth) V7X found on USB."""
        # Load HIDDevice DLL first so it is in memory when HIDtoUART is loaded
        ctypes.WinDLL(_SLAB_DEV_DLL)
        dll = ctypes.WinDLL(_SLAB_HID_DLL)
        self._dll = dll

        # Count devices
        num = ctypes.c_uint32(0)
        status = dll.HidUart_GetNumDevices(
            ctypes.byref(num), ctypes.c_uint16(V7X_VID), ctypes.c_uint16(V7X_PID)
        )
        if status != HID_UART_SUCCESS:
            raise V71Error(f"HidUart_GetNumDevices failed: status={status}")
        if num.value == 0:
            raise V71Error("No Vitrek V7X devices found on USB.")

        # Open device
        handle = ctypes.c_void_p(0)
        status = dll.HidUart_Open(
            ctypes.byref(handle),
            ctypes.c_uint32(device_index),
            ctypes.c_uint16(V7X_VID),
            ctypes.c_uint16(V7X_PID),
        )
        if status != HID_UART_SUCCESS:
            raise V71Error(f"HidUart_Open failed: status={status}")

        # Configure UART (must match V7X internal settings – do not change)
        status = dll.HidUart_SetUartConfig(
            handle,
            ctypes.c_uint32(115200),
            ctypes.c_uint8(HID_UART_EIGHT_DATA_BITS),
            ctypes.c_uint8(HID_UART_NO_PARITY),
            ctypes.c_uint8(HID_UART_SHORT_STOP_BIT),
            ctypes.c_uint8(HID_UART_RTS_CTS_FLOW_CONTROL),
        )
        if status != HID_UART_SUCCESS:
            raise V71Error(f"HidUart_SetUartConfig failed: status={status}")

        # Generous timeouts: 2 s read, 1 s write
        dll.HidUart_SetTimeouts(handle, ctypes.c_uint32(2000), ctypes.c_uint32(1000))

        # Flush any stale data
        dll.HidUart_FlushBuffers(handle, ctypes.c_bool(True), ctypes.c_bool(True))

        self._hid_handle = handle
        self._mode = "usb"

        # Always reset/clear on connect
        self._raw_send("*RST")
        time.sleep(0.1)

    def connect_serial(self, port: str, baud: int = 115200) -> None:
        """Open an RS-232 / virtual COM port."""
        import serial
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            rtscts=True,
            timeout=2.0,
            write_timeout=1.0,
        )
        ser.dtr = True
        self._serial = ser
        self._mode = "serial"
        self._raw_send("*RST")
        time.sleep(0.1)

    def disconnect(self) -> None:
        """Close the connection."""
        with self._lock:
            if self._mode == "usb" and self._hid_handle and self._dll:
                self._dll.HidUart_Close(self._hid_handle)
                self._hid_handle = None
            elif self._mode == "serial" and self._serial:
                self._serial.close()
                self._serial = None
            self._mode = None

    @property
    def connected(self) -> bool:
        return self._mode is not None

    # ------------------------------------------------------------------
    # Low-level send / receive
    # ------------------------------------------------------------------

    def _raw_send(self, cmd: str) -> None:
        """Send a command string (adds \\r\\n terminator)."""
        data = (cmd + "\r\n").encode("ascii")
        if self._mode == "usb":
            buf = (ctypes.c_uint8 * len(data))(*data)
            written = ctypes.c_uint32(0)
            status = self._dll.HidUart_Write(
                self._hid_handle, buf, ctypes.c_uint32(len(data)),
                ctypes.byref(written)
            )
            if status != HID_UART_SUCCESS:
                raise V71Error(f"HidUart_Write failed: status={status}")
        elif self._mode == "serial":
            self._serial.write(data)

    def _raw_recv(self, timeout_s: float = 2.0) -> str:
        """Read bytes until \\n, return decoded string (strips \\r\\n)."""
        deadline = time.monotonic() + timeout_s
        buf = bytearray()
        if self._mode == "usb":
            one = (ctypes.c_uint8 * 1)()
            read = ctypes.c_uint32(0)
            while time.monotonic() < deadline:
                self._dll.HidUart_Read(
                    self._hid_handle, one, ctypes.c_uint32(1), ctypes.byref(read)
                )
                if read.value:
                    buf.append(one[0])
                    if one[0] == ord('\n'):
                        break
            else:
                raise V71Error("Read timeout waiting for response from V71.")
        elif self._mode == "serial":
            line = self._serial.readline()
            if not line:
                raise V71Error("Read timeout waiting for response from V71.")
            buf = bytearray(line)
        return buf.decode("ascii", errors="replace").strip()

    def send_command(self, cmd: str) -> None:
        """Send a command that produces no response."""
        with self._lock:
            self._raw_send(cmd)

    def query(self, cmd: str, timeout_s: float = 2.0) -> str:
        """Send a query command and return the response string."""
        with self._lock:
            self._raw_send(cmd)
            return self._raw_recv(timeout_s)

    def check_error(self) -> int:
        """Query *ERR? and return the error code (0 = no error)."""
        resp = self.query("*ERR?")
        try:
            return int(resp.strip())
        except ValueError:
            return -1

    # ------------------------------------------------------------------
    # High-level V7X commands
    # ------------------------------------------------------------------

    def identify(self) -> dict:
        """Return *IDN? parsed as {manufacturer, model, serial}."""
        resp = self.query("*IDN?")
        parts = [p.strip() for p in resp.split(",")]
        return {
            "manufacturer": parts[0] if len(parts) > 0 else "",
            "model":        parts[1] if len(parts) > 1 else "",
            "serial":       parts[2] if len(parts) > 2 else "",
            "firmware":     parts[3] if len(parts) > 3 else "",
        }

    def reset(self) -> None:
        """Send *RST – aborts test, clears active sequence, resets interface."""
        self.send_command("*RST")
        time.sleep(0.1)

    def clear(self) -> None:
        """Send *CLS – clears ERR register, resets front panel to LOCAL."""
        self.send_command("*CLS")

    def lockout(self) -> None:
        """Lock front panel (REMOTE LOCKOUT)."""
        self.send_command("LOCKOUT")

    def local(self) -> None:
        """Return front panel to LOCAL state."""
        self.send_command("LOCAL")

    # --- Sequence management ---

    def new_sequence(self) -> None:
        """Clear sequence #0 and set it as active (NOSEQ)."""
        self.send_command("NOSEQ")
        err = self.check_error()
        if err:
            raise V71Error(f"NOSEQ failed with error code {err}")

    def add_step(self, step_cmd: str) -> None:
        """
        Add a test step to the active sequence.
        step_cmd is the full ADD,... string, e.g.:
            'ADD,ACW,1000.0,1.5,60.0,,0.005'
        """
        _ERR_DESCRIPTIONS = {
            1: "command could not be decoded at this time (is a test running?)",
            2: "step type not supported by this instrument model",
            3: "numeric value out of allowable range",
            4: "field syntax error",
            5: "missing required field",
            6: "too many fields",
            7: "unknown command keyword",
        }
        self.send_command(step_cmd)
        err = self.check_error()
        if err:
            desc = _ERR_DESCRIPTIONS.get(err, f"error code {err}")
            raise V71Error(f"ADD step rejected — {desc}. Command: {step_cmd}")

    def add_acw_step(self, voltage_v: float, ramp_s: float, dwell_s: float,
                     max_leakage_a: float = 0.005, min_leakage_a: float = None,
                     grounded: bool = False) -> None:
        """
        Add an AC Withstand (ACW) test step.

        Pass max_leakage_a=None for the front panel's "Breakdown Only" setting.
        Per the manual, with both limits set to NONE the V7X still detects
        breakdown in accordance with most standards — it simply does not apply
        a numeric pass/fail window to the leakage reading.
        """
        min_field = f"{min_leakage_a}" if min_leakage_a is not None else ""
        max_field = f"{max_leakage_a}" if max_leakage_a is not None else ""
        gnd_field = ",GND" if grounded else ""
        cmd = f"ADD,ACW,{voltage_v},{ramp_s},{dwell_s},{min_field},{max_field}{gnd_field}"
        self.add_step(cmd)

    def add_dcw_step(self, voltage_v: float, ramp_s: float, dwell_s: float,
                     max_leakage_a: float = 25e-6, min_leakage_a: float = None,
                     grounded: bool = False, capacitive: bool = False) -> None:
        """
        Add a DC Withstand (DCW) test step.

        Manual field order (DCW CONFIGURATION FIELDS):
            1 DCW | 2 voltage | 3 ramp | 4 dwell | 5 min leakage | 6 max leakage
            7 '' = isolated DUT, 'GND' = grounded DUT
            8 '' = resistive DUT, 'CAP' = capacitive DUT

        Fields 7 and 8 are positional. Field 8 may only be omitted if field 7
        is omitted too, so setting CAP on an isolated DUT still requires an
        empty field 7 to hold its place.
        """
        min_field = f"{min_leakage_a}" if min_leakage_a is not None else ""
        max_field = f"{max_leakage_a}" if max_leakage_a is not None else ""
        cmd = f"ADD,DCW,{voltage_v},{ramp_s},{dwell_s},{min_field},{max_field}"
        if grounded or capacitive:
            cmd += f",{'GND' if grounded else ''},{'CAP' if capacitive else ''}"
        self.add_step(cmd)

    def add_ir_step(self, voltage_v: float, dwell_s: float,
                    min_resistance_ohm: float = 100e6,
                    max_resistance_ohm: float = None,
                    precheck_delay_s: float = 0.0,
                    grounded: bool = False,
                    capacitive: bool = False) -> None:
        """
        Add an Insulation Resistance (IR) test step.

        Manual field order (IR CONFIGURATION FIELDS):
            1 IR | 2 voltage | 3 dwell | 4 pre-check delay
            5 min resistance | 6 max resistance
            7 '' = isolated DUT, 'GND' = grounded DUT
            8 '' = resistive DUT, 'CAP' = capacitive DUT
        """
        max_field = f"{max_resistance_ohm}" if max_resistance_ohm is not None else ""
        cmd = (f"ADD,IR,{voltage_v},{dwell_s},{precheck_delay_s},"
               f"{min_resistance_ohm},{max_field}")
        if grounded or capacitive:
            cmd += f",{'GND' if grounded else ''},{'CAP' if capacitive else ''}"
        self.add_step(cmd)

    def add_gb_step(self, current_a: float, dwell_s: float,
                    max_ohm: float = 0.1, min_ohm: float = None) -> None:
        """Add a Ground Bond (GB) test step."""
        min_field = f"{min_ohm}" if min_ohm is not None else ""
        cmd = f"ADD,GB,{current_a},{dwell_s},{min_field},{max_ohm}"
        self.add_step(cmd)

    def add_cont_step(self, test_time_s: float,
                      min_ohm: float = None, max_ohm: float = None) -> None:
        """Add a Continuity (CONT) test step."""
        min_field = f"{min_ohm}" if min_ohm is not None else ""
        max_field = f"{max_ohm}" if max_ohm is not None else ""
        cmd = f"ADD,CONT,{test_time_s},{min_field},{max_field}"
        self.add_step(cmd)

    def add_pause_step(self, pause_s: float) -> None:
        """
        Add a PAUSE step — the sequence simply waits, no operator action needed.
        Manual: ADD,PAUSE,<seconds>
        """
        self.add_step(f"ADD,PAUSE,{pause_s}")

    def add_hold_step(self, timeout_s: float, line1: str = "", line2: str = "") -> None:
        """
        Add a HOLD step — the V7X displays up to two message lines on the front
        panel and waits for the operator (or a CONT command) before continuing.

        Used by the PVD verification runner to prompt for a lead change between
        verification phases without tearing down the sequence.

        Manual: ADD,HOLD,<timeout_s>,<1st message line>,<2nd message line>

        The V7X character set for on-screen messages is limited to
        ABCDEFGHIJKLMNOPQRSTUVWXYZ#%&:()*+-./<=>@0123456789 and space, so the
        message lines are upper-cased and stripped of anything else. Commas are
        removed too — they are the protocol field separator.
        """
        self.add_step(
            f"ADD,HOLD,{timeout_s},{sanitize_message(line1)},{sanitize_message(line2)}"
        )

    # --- Instrument configuration backup / restore ---
    #
    # The V7X exposes a query form for each of its global configuration
    # settings, so the configuration CAN be snapshotted and replayed.
    #
    # Two deliberate gaps, both documented in CONFIG_BACKUP_GAPS below:
    #   * IFACE (RS232 vs USB) has no remote command at all — by design, since
    #     changing it remotely could sever the connection issuing the command.
    #   * The CONT ZERO and GB ZERO lead-resistance offsets (UTILITY MENU) have
    #     no remote command either, and neither does the per-step ZERO offset
    #     in the sequence editor. These must be re-measured by hand.
    #
    # There is NO way to read a test sequence back out of the instrument. The
    # sequence commands (NOSEQ / ADD / NAME / RCL / SAVE) are write-only and
    # the protocol has no query returning step definitions. A config backup is
    # therefore a backup of settings, never of sequences.

    CONFIG_QUERIES = {
        "VICL":      ("VICL?",      int,   "Number of 964 switch matrix units (0-4)"),
        "DIO":       ("DIO?",       int,   "Digital I/O input enable (0=none, 1=interlock, 2=start/stop, 3=all)"),
        "START":     ("START?",     int,   "Front panel START behaviour (0=stop first, 1=no stop, 2=disabled)"),
        "BEEP":      ("BEEP?",      int,   "Beeper (0=off, 1=start/stop, 2=keys, 3=all)"),
        "FREQ":      ("FREQ?",      int,   "ACW/GB test frequency in Hz (50 or 60)"),
        "ARC":       ("ARC?",       int,   "Arc current limit (0=disabled)"),
        "IREND":     ("IREND?",     int,   "IR end-on (0=fail, 1=pass, 2=time only, 3=pass and steady/increasing)"),
        "RAMPDOWN":  ("RAMPDOWN?",  int,   "Ramp down (0=fast, 1=as ramp)"),
        "CONTFAIL":  ("CONTFAIL?",  int,   "Continue sequence on failure (0=no, 1=yes)"),
    }

    CONFIG_BACKUP_GAPS = [
        "IFACE (RS232 vs USB) — no remote command; set from the CONFIG MENU screen.",
        "CONT ZERO lead-resistance offset — no remote command; re-measure from UTILITY MENU -> CONT ZERO.",
        "GB ZERO lead-resistance offset — no remote command; re-measure from UTILITY MENU -> GB ZERO.",
        "Per-step ZERO offsets inside CONT/GB sequence steps — not settable over the interface at all.",
        "Test sequences — the protocol is write-only for sequences; they cannot be read back.",
        "CONFIG MENU lock password — no remote command.",
    ]

    def backup_config(self) -> dict:
        """
        Snapshot every remotely-readable configuration setting.

        Returns a dict with the instrument identity, the settings, and an
        explicit list of what this backup does NOT cover, so a restore is
        never mistaken for a complete recovery.
        """
        settings = {}
        errors = {}
        for key, (cmd, caster, _desc) in self.CONFIG_QUERIES.items():
            try:
                raw = self.query(cmd).strip()
                settings[key] = caster(raw)
            except Exception as exc:                       # noqa: BLE001
                # A model without a setting (e.g. VICL on a V75) answers with
                # an error rather than a value. Record it instead of aborting
                # the whole backup.
                errors[key] = str(exc)
        return {
            "instrument":   self.identify(),
            "settings":     settings,
            "unreadable":   errors,
            "descriptions": {k: v[2] for k, v in self.CONFIG_QUERIES.items()},
            "not_covered":  list(self.CONFIG_BACKUP_GAPS),
        }

    def restore_config(self, backup: dict, dry_run: bool = False) -> dict:
        """
        Replay a snapshot from backup_config() back into the instrument.

        Returns a per-setting report. With dry_run=True the commands are built
        and returned but never sent, so the operator can review exactly what
        would change before anything is written.
        """
        settings = (backup or {}).get("settings") or {}
        if not settings:
            raise V71Error("Backup contains no settings to restore.")

        current = {}
        if not dry_run:
            # Read first so the report can show what actually changed.
            for key in settings:
                if key in self.CONFIG_QUERIES:
                    try:
                        current[key] = self.CONFIG_QUERIES[key][1](
                            self.query(self.CONFIG_QUERIES[key][0]).strip())
                    except Exception:                      # noqa: BLE001
                        current[key] = None

        report = {"applied": [], "skipped": [], "failed": [], "dry_run": dry_run}
        for key, value in settings.items():
            if key not in self.CONFIG_QUERIES:
                report["skipped"].append({key: "not a known configuration setting"})
                continue
            cmd = f"{key},{value}"
            if dry_run:
                report["applied"].append({"setting": key, "command": cmd})
                continue
            try:
                self.send_command(cmd)
                err = self.check_error()
                if err:
                    report["failed"].append({"setting": key, "command": cmd,
                                             "error_code": err})
                else:
                    report["applied"].append({"setting": key, "command": cmd,
                                              "was": current.get(key), "now": value})
            except Exception as exc:                       # noqa: BLE001
                report["failed"].append({"setting": key, "command": cmd,
                                         "error": str(exc)})
        report["not_covered"] = list(self.CONFIG_BACKUP_GAPS)
        return report

    def name_sequence(self, name: str) -> None:
        """Set the name of the active test sequence."""
        self.send_command(f"NAME,{name}")

    def save_sequence(self, store_num: int) -> None:
        """Save active sequence to non-volatile store #."""
        self.send_command(f"SAVE,{store_num}")

    def recall_sequence(self, store_num: int) -> None:
        """Recall a stored sequence and make it active."""
        self.send_command(f"RCL,{store_num}")

    # --- Test execution ---

    def run(self) -> None:
        """Start the active test sequence."""
        self.send_command("RUN")

    def abort(self) -> None:
        """Abort a running test sequence."""
        self.send_command("ABORT")

    def cont(self) -> None:
        """Continue from a HOLD step or user-terminated dwell."""
        self.send_command("CONT")

    # --- Status queries ---

    def is_running(self) -> bool:
        """Return True if a test sequence is currently executing."""
        return self.query("RUN?").strip() == "1"

    def active_seq_number(self) -> int:
        return int(self.query("SEQ?").strip())

    def current_step(self) -> int:
        """Return the currently executing step number (0 if not running)."""
        return int(self.query("STEP?").strip())

    def overall_result(self) -> int:
        """
        Return the RSLT? bitmask (0 = pass, non-zero = failure).
        See TEST STEP STATUS FLAGS in the manual for bit meanings.
        """
        return int(self.query("RSLT?").strip())

    def step_status_string(self) -> str:
        """
        Return the STAT? string: one character per step.
        P=passed, F=failed, -=not performed, ?=in process
        """
        return self.query("STAT?").strip()

    def step_result(self, step_num: int) -> dict:
        """
        Return parsed STEPRSLT? for the given step number (1-based).
        Fields: phase, elapsed_s, status_flags, level, breakdown_a, measurement, arc_a
        """
        resp = self.query(f"STEPRSLT?,{step_num}")
        parts = [p.strip() for p in resp.split(",")]

        phase_map = {
            "0": "not_executed",
            "1": "terminated_before_start",
            "2": "terminated_during_ramp",
            "3": "terminated_during_dwell",
        }

        def _float(s):
            try:
                return float(s) if s else None
            except ValueError:
                return None

        return {
            "step":          step_num,
            "phase":         phase_map.get(parts[0], f"unknown({parts[0]})") if parts else None,
            "elapsed_s":     _float(parts[1]) if len(parts) > 1 else None,
            "status_flags":  int(parts[2]) if len(parts) > 2 and parts[2] else 0,
            "level":         _float(parts[3]) if len(parts) > 3 else None,
            "breakdown_a":   _float(parts[4]) if len(parts) > 4 else None,
            "measurement":   _float(parts[5]) if len(parts) > 5 else None,
            "arc_a":         _float(parts[6]) if len(parts) > 6 else None,
            "passed":        (int(parts[2]) == 0) if len(parts) > 2 and parts[2] else None,
        }

    def live_measurement(self, quantity: str) -> float:
        """
        Query a live measurement during a running step.
        quantity: 'AMPS', 'VOLTS', 'OHMS', 'FREQ', or 'ARC'
        """
        resp = self.query(f"MEASRSLT?,{quantity}")
        return float(resp.strip())

    def decode_status_flags(self, flags: int) -> list[str]:
        """Return human-readable list of status flag descriptions."""
        flag_map = {
            1:     "Internal fault",
            2:     "Over voltage output",
            4:     "Line too low",
            8:     "DUT breakdown detected",
            16:    "HOLD step timeout",
            32:    "User aborted",
            64:    "GB over-compliance",
            128:   "Arc detected",
            256:   "Below minimum limit",
            512:   "Above maximum limit",
            1024:  "IR steady/decreasing current not detected",
            2048:  "INTERLOCK failure",
            4096:  "Switch matrix error",
            8192:  "V7X overheated",
            16384: "DUT voltage/current could not be controlled",
            32768: "Wiring error in GB step",
            65536: "Drive voltage instability or wildly varying measurement",
        }
        return [desc for bit, desc in flag_map.items() if flags & bit]
