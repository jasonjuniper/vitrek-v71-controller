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
        self.send_command(step_cmd)
        err = self.check_error()
        if err:
            raise V71Error(f"ADD step failed with error code {err}: {step_cmd}")

    def add_acw_step(self, voltage_v: float, ramp_s: float, dwell_s: float,
                     max_leakage_a: float = 0.005, min_leakage_a: float = None,
                     grounded: bool = False) -> None:
        """Add an AC Withstand (ACW) test step."""
        min_field = f"{min_leakage_a}" if min_leakage_a is not None else ""
        gnd_field = ",GND" if grounded else ""
        cmd = f"ADD,ACW,{voltage_v},{ramp_s},{dwell_s},{min_field},{max_leakage_a}{gnd_field}"
        self.add_step(cmd)

    def add_dcw_step(self, voltage_v: float, ramp_s: float, dwell_s: float,
                     max_leakage_a: float = 25e-6, min_leakage_a: float = None,
                     grounded: bool = False, capacitive: bool = False) -> None:
        """Add a DC Withstand (DCW) test step."""
        min_field = f"{min_leakage_a}" if min_leakage_a is not None else ""
        gnd_field = "GND" if grounded else ""
        cap_field = ",CAP" if capacitive else ""
        gnd_sep = "," if grounded or capacitive else ""
        cmd = f"ADD,DCW,{voltage_v},{ramp_s},{dwell_s},{min_field},{max_leakage_a},{gnd_sep}{gnd_field}{cap_field}"
        self.add_step(cmd)

    def add_ir_step(self, voltage_v: float, dwell_s: float,
                    min_resistance_ohm: float = 100e6,
                    max_resistance_ohm: float = None,
                    precheck_delay_s: float = 0.0,
                    grounded: bool = False) -> None:
        """Add an Insulation Resistance (IR) test step."""
        max_field = f"{max_resistance_ohm}" if max_resistance_ohm is not None else ""
        gnd_field = ",GND" if grounded else ""
        cmd = f"ADD,IR,{voltage_v},{dwell_s},{precheck_delay_s},{min_resistance_ohm},{max_field}{gnd_field}"
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
