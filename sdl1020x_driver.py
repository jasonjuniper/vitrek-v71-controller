"""
sdl1020x_driver.py
------------------
Python SCPI driver for the Siglent SDL1000X-E series DC Electronic Load.
Tested against: SDL1020X-E (200 W, 150 V, 30 A).

Transport backends:
  1. TCP/LAN    — Ethernet, port 5025.
  2. USB-VISA   — USBTMC via NI-VISA (pip install pyvisa). Device enumerates
                  as "USB Test and Measurement Device (IVI)" in Device Manager.
  3. USB CDC    — Virtual COM port (requires Siglent CDC driver).

SCPI reference: SDL1000X-E Programming Guide (Siglent, 2022)
"""

import socket
import threading
import time
from typing import Optional


class SDL1020XError(Exception):
    pass


class SDL1020XDriver:
    MAX_VOLTAGE_V  = 150.0
    MAX_CURRENT_A  = 30.0
    MAX_POWER_W    = 200.0
    MAX_RESIST_OHM = 10000.0

    def __init__(self):
        self._lock      = threading.Lock()
        self._mode: Optional[str] = None   # "tcp" | "visa" | "serial"
        self._sock: Optional[socket.socket] = None
        self._serial    = None
        self._visa_inst = None
        self._recv_buf  = b""

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect_tcp(self, host: str, port: int = 5025, timeout: float = 5.0) -> None:
        with self._lock:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            try:
                s.connect((host, port))
            except OSError as e:
                raise SDL1020XError(f"TCP connect to {host}:{port} failed: {e}") from e
            self._sock = s
            self._mode = "tcp"
        self._flush()

    def connect_visa(self, resource: str = "") -> None:
        """
        Connect via USB-VISA (USBTMC). NI-VISA must be installed.
        If resource is empty, auto-selects the first Siglent instrument.
        """
        try:
            import pyvisa
        except ImportError:
            raise SDL1020XError(
                "pyvisa not installed. Run: pip install pyvisa"
            )
        rm = pyvisa.ResourceManager()
        if not resource:
            for r in rm.list_resources():
                if "F4EC" in r.upper() or "SIGLENT" in r.upper():
                    resource = r
                    break
            if not resource:
                usb = [r for r in rm.list_resources() if "USB" in r.upper()]
                resource = usb[0] if usb else ""
            if not resource:
                raise SDL1020XError(
                    "No VISA/USBTMC instruments found. "
                    "Check NI-VISA is installed and the SDL is connected."
                )
        try:
            inst = rm.open_resource(resource)
            inst.timeout           = 15000  # 15 s — USBTMC can be slow on first connect
            # Do NOT set read_termination for USBTMC — NI-VISA uses the EOM bit.
            # Setting it to "\n" causes VI_ERROR_TMO if the SDL omits the newline.
            inst.read_termination  = ""
            inst.write_termination = "\n"
        except Exception as e:
            raise SDL1020XError(f"VISA open failed for '{resource}': {e}") from e
        self._visa_inst = inst
        self._mode = "visa"
        self._recv_buf = b""
        time.sleep(0.3)  # allow USBTMC enumeration to settle before first query

    def connect_serial(self, port: str, baud: int = 115200, timeout: float = 2.0) -> None:
        import serial
        ser = serial.Serial(port=port, baudrate=baud,
                            bytesize=8, parity="N", stopbits=1,
                            timeout=timeout, write_timeout=2.0)
        self._serial = ser
        self._mode = "serial"
        self._flush()

    @staticmethod
    def list_visa_resources() -> list[dict]:
        try:
            import pyvisa
            rm = pyvisa.ResourceManager()
            return [{"resource": r, "is_usbtmc": "USB" in r.upper()}
                    for r in rm.list_resources()]
        except Exception as e:
            return [{"resource": str(e), "is_usbtmc": False, "error": True}]

    def disconnect(self) -> None:
        with self._lock:
            if self._sock:
                try: self._sock.close()
                except OSError: pass
                self._sock = None
            if self._serial:
                try: self._serial.close()
                except Exception: pass
                self._serial = None
            if self._visa_inst:
                try: self._visa_inst.close()
                except Exception: pass
                self._visa_inst = None
            self._mode = None

    @property
    def connected(self) -> bool:
        return self._mode is not None

    # ------------------------------------------------------------------
    # Low-level SCPI transport
    # ------------------------------------------------------------------

    def _send(self, cmd: str) -> None:
        data = (cmd.strip() + "\n").encode("ascii")
        if self._mode == "tcp":
            self._sock.sendall(data)
        elif self._mode == "serial":
            self._serial.write(data)
        elif self._mode == "visa":
            self._visa_inst.write(cmd.strip())
        else:
            raise SDL1020XError("Not connected.")

    def _recv_line(self, timeout: float = 5.0) -> str:
        if self._mode == "visa":
            try:
                return self._visa_inst.read().strip()
            except Exception as e:
                raise SDL1020XError(f"VISA read failed: {e}") from e
        deadline = time.monotonic() + timeout
        while True:
            if b"\n" in self._recv_buf:
                line, self._recv_buf = self._recv_buf.split(b"\n", 1)
                return line.decode("ascii", errors="replace").strip()
            if time.monotonic() > deadline:
                raise SDL1020XError("SCPI read timeout — no response from SDL.")
            if self._mode == "tcp":
                try:
                    chunk = self._sock.recv(4096)
                    if chunk:
                        self._recv_buf += chunk
                except socket.timeout:
                    raise SDL1020XError("TCP read timeout.")
            elif self._mode == "serial":
                self._recv_buf += self._serial.read(256)

    def _flush(self) -> None:
        self._recv_buf = b""
        if self._mode == "tcp" and self._sock:
            self._sock.settimeout(0.1)
            try:
                while True:
                    if not self._sock.recv(4096):
                        break
            except (socket.timeout, OSError):
                pass
            self._sock.settimeout(5.0)
        elif self._mode == "serial" and self._serial:
            self._serial.reset_input_buffer()
        # Note: do NOT call inst.clear() for VISA — it sends a USBTMC CLEAR
        # which resets the SDL's USB state and causes the next query to time out.

    def write(self, cmd: str) -> None:
        with self._lock:
            self._send(cmd)

    def query(self, cmd: str, timeout: float = 5.0) -> str:
        with self._lock:
            if self._mode == "visa":
                try:
                    return self._visa_inst.query(cmd.strip()).strip()
                except Exception as e:
                    raise SDL1020XError(f"VISA query failed: {e}") from e
            self._send(cmd)
            return self._recv_line(timeout)

    # ------------------------------------------------------------------
    # Instrument identification & reset
    # ------------------------------------------------------------------

    def identify(self) -> dict:
        resp  = self.query("*IDN?")
        parts = [p.strip() for p in resp.split(",")]
        return {
            "manufacturer": parts[0] if len(parts) > 0 else "",
            "model":        parts[1] if len(parts) > 1 else "",
            "serial":       parts[2] if len(parts) > 2 else "",
            "firmware":     parts[3] if len(parts) > 3 else "",
        }

    def reset(self) -> None:
        self.write("*RST")
        time.sleep(0.5)

    def clear_status(self) -> None:
        self.write("*CLS")

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def input_on(self) -> None:
        self.write(":INPut ON")

    def input_off(self) -> None:
        self.write(":INPut OFF")

    def is_input_on(self) -> bool:
        return self.query(":INPut?").strip().upper() in ("ON", "1")

    def set_input(self, state: bool) -> None:
        self.input_on() if state else self.input_off()

    # ------------------------------------------------------------------
    # Mode selection
    # ------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        mode = mode.upper()
        if mode not in {"CC", "CV", "CR", "CP"}:
            raise SDL1020XError(f"Invalid mode '{mode}'. Choose CC/CV/CR/CP.")
        self.write(f":SOURce:FUNCtion {mode}")

    def get_mode(self) -> str:
        return self.query(":SOURce:FUNCtion?").strip().upper()

    # ------------------------------------------------------------------
    # Set-points
    # ------------------------------------------------------------------

    def set_current(self, amps: float) -> None:
        if not (0 <= amps <= self.MAX_CURRENT_A):
            raise SDL1020XError(f"Current {amps} A out of range")
        self.write(f":SOURce:CURRent {amps:.6f}")

    def set_voltage(self, volts: float) -> None:
        if not (0 <= volts <= self.MAX_VOLTAGE_V):
            raise SDL1020XError(f"Voltage {volts} V out of range")
        self.write(f":SOURce:VOLTage {volts:.6f}")

    def set_resistance(self, ohms: float) -> None:
        if not (0.08 <= ohms <= self.MAX_RESIST_OHM):
            raise SDL1020XError(f"Resistance {ohms} Ω out of range")
        self.write(f":SOURce:RESistance {ohms:.6f}")

    def set_power(self, watts: float) -> None:
        if not (0 <= watts <= self.MAX_POWER_W):
            raise SDL1020XError(f"Power {watts} W out of range")
        self.write(f":SOURce:POWer {watts:.6f}")

    def get_setpoint(self, mode: Optional[str] = None) -> float:
        if mode is None:
            mode = self.get_mode()
        cmd_map = {
            "CC": ":SOURce:CURRent?",
            "CV": ":SOURce:VOLTage?",
            "CR": ":SOURce:RESistance?",
            "CP": ":SOURce:POWer?",
        }
        return float(self.query(cmd_map[mode.upper()]))

    # ------------------------------------------------------------------
    # Protection limits
    # ------------------------------------------------------------------

    def set_ovp(self, volts: float) -> None:
        self.write(f":SOURce:VOLTage:PROTection:LEVel {volts:.3f}")

    def set_ocp(self, amps: float) -> None:
        self.write(f":SOURce:CURRent:PROTection:LEVel {amps:.3f}")

    # ------------------------------------------------------------------
    # Measurements
    # ------------------------------------------------------------------

    def measure_voltage(self) -> float:
        return float(self.query(":MEASure:VOLTage?"))

    def measure_current(self) -> float:
        return float(self.query(":MEASure:CURRent?"))

    def measure_power(self) -> float:
        return float(self.query(":MEASure:POWer?"))

    def measure_resistance(self) -> float:
        return float(self.query(":MEASure:RESistance?"))

    def measure_all(self) -> dict:
        return {
            "voltage_v":      self.measure_voltage(),
            "current_a":      self.measure_current(),
            "power_w":        self.measure_power(),
            "resistance_ohm": self.measure_resistance(),
        }

    # ------------------------------------------------------------------
    # Timer / battery
    # ------------------------------------------------------------------

    def set_timer_current(self, amps: float, duration_s: float) -> None:
        self.write(f":TIMEr:CURRent {amps:.3f},{duration_s:.3f}")

    def timer_on(self) -> None:
        self.write(":TIMEr:ENABle ON")

    def timer_off(self) -> None:
        self.write(":TIMEr:ENABle OFF")

    def get_timer_elapsed(self) -> float:
        return float(self.query(":TIMEr:TIME?"))

    # ------------------------------------------------------------------
    # Short circuit
    # ------------------------------------------------------------------

    def short_on(self) -> None:
        self.write(":SHORt:ENABle ON")

    def short_off(self) -> None:
        self.write(":SHORt:ENABle OFF")

    # ------------------------------------------------------------------
    # System / status
    # ------------------------------------------------------------------

    def get_errors(self) -> str:
        return self.query(":SYSTem:ERRor?").strip()

    def get_protection_status(self) -> dict:
        raw = self.query(":STATus:QUEStionable:CONDition?")
        try:
            flags = int(raw)
        except ValueError:
            return {"raw": raw}
        return {
            "ovp": bool(flags & (1 << 0)),
            "ocp": bool(flags & (1 << 1)),
            "opp": bool(flags & (1 << 2)),
            "otp": bool(flags & (1 << 3)),
            "raw_flags": flags,
        }


def _mode_scpi(mode: str) -> str:
    return {"CC": "CURRent", "CV": "VOLTage", "CR": "RESistance", "CP": "POWer"}[mode.upper()]
