"""
sdl1020x_driver.py
------------------
Python SCPI driver for the Siglent SDL1000X-E series DC Electronic Load.
Tested against: SDL1020X-E (200 W, 150 V, 30 A).

Transport backends (in order of preference):
  1. TCP/LAN  — connect via Ethernet to port 5025 (standard SCPI port).
               Set a static IP on the SDL via SYSTEM > INTERFACE > LAN.
  2. USB CDC  — SDL enumerates as a virtual COM port on Windows/Linux.
               Baud rate is irrelevant (CDC), but we set 115200 for compat.

SCPI reference: SDL1000X-E Programming Guide (Siglent, 2022)

Quick-start:
    d = SDL1020XDriver()
    d.connect_tcp("192.168.1.50")      # LAN
    # -- or --
    d.connect_serial("COM4")           # USB CDC

    print(d.identify())
    d.set_mode("CC")
    d.set_current(1.0)                 # 1 A constant-current load
    d.input_on()
    time.sleep(2)
    print(d.measure_all())
    d.input_off()
    d.disconnect()
"""

import socket
import threading
import time
from typing import Optional


class SDL1020XError(Exception):
    pass


class SDL1020XDriver:
    """
    Thread-safe driver for the Siglent SDL1020X-E DC Electronic Load.

    Modes:
      CC  — Constant Current
      CV  — Constant Voltage
      CR  — Constant Resistance
      CP  — Constant Power

    All set-point values are in SI units:
      Current:    Amps (A)
      Voltage:    Volts (V)
      Resistance: Ohms (Ω)
      Power:      Watts (W)
    """

    # SDL1020X-E hardware limits
    MAX_VOLTAGE_V  = 150.0
    MAX_CURRENT_A  = 30.0
    MAX_POWER_W    = 200.0
    MAX_RESIST_OHM = 10000.0

    def __init__(self):
        self._lock   = threading.Lock()
        self._mode: Optional[str] = None   # "tcp" or "serial"
        self._sock: Optional[socket.socket] = None
        self._serial = None
        self._recv_buf = b""

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect_tcp(self, host: str, port: int = 5025, timeout: float = 5.0) -> None:
        """Connect to SDL via Ethernet (LAN). Set static IP on the SDL first."""
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

    def connect_serial(self, port: str, baud: int = 115200, timeout: float = 2.0) -> None:
        """Connect to SDL via USB CDC virtual COM port."""
        import serial
        ser = serial.Serial(port=port, baudrate=baud,
                            bytesize=8, parity="N", stopbits=1,
                            timeout=timeout, write_timeout=2.0)
        self._serial = ser
        self._mode = "serial"
        self._flush()

    def disconnect(self) -> None:
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            if self._serial:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
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
        else:
            raise SDL1020XError("Not connected.")

    def _recv_line(self, timeout: float = 3.0) -> str:
        deadline = time.monotonic() + timeout
        while True:
            # Check buffered data first
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
                chunk = self._serial.read(256)
                self._recv_buf += chunk

    def _flush(self) -> None:
        """Discard any stale bytes in the receive buffer."""
        self._recv_buf = b""
        if self._mode == "tcp" and self._sock:
            self._sock.settimeout(0.1)
            try:
                while True:
                    data = self._sock.recv(4096)
                    if not data:
                        break
            except (socket.timeout, OSError):
                pass
            self._sock.settimeout(5.0)
        elif self._mode == "serial" and self._serial:
            self._serial.reset_input_buffer()

    def write(self, cmd: str) -> None:
        """Send a command that produces no response."""
        with self._lock:
            self._send(cmd)

    def query(self, cmd: str, timeout: float = 3.0) -> str:
        """Send a query command and return the response string."""
        with self._lock:
            self._send(cmd)
            return self._recv_line(timeout)

    # ------------------------------------------------------------------
    # Instrument identification & reset
    # ------------------------------------------------------------------

    def identify(self) -> dict:
        """Return *IDN? parsed as {manufacturer, model, serial, firmware}."""
        resp = self.query("*IDN?")
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
    # Input (load connection)
    # ------------------------------------------------------------------

    def input_on(self) -> None:
        """Connect the load to the DUT terminals (enable input)."""
        self.write(":INPut ON")

    def input_off(self) -> None:
        """Disconnect the load from the DUT terminals (disable input)."""
        self.write(":INPut OFF")

    def is_input_on(self) -> bool:
        return self.query(":INPut?").strip().upper() in ("ON", "1")

    # ------------------------------------------------------------------
    # Mode selection
    # ------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """
        Set the operating mode.
        mode: 'CC' | 'CV' | 'CR' | 'CP'
        """
        mode = mode.upper()
        valid = {"CC", "CV", "CR", "CP"}
        if mode not in valid:
            raise SDL1020XError(f"Invalid mode '{mode}'. Choose from {valid}")
        self.write(f":SOURce:FUNCtion {mode}")

    def get_mode(self) -> str:
        return self.query(":SOURce:FUNCtion?").strip().upper()

    # ------------------------------------------------------------------
    # Set-point configuration
    # ------------------------------------------------------------------

    def set_current(self, amps: float) -> None:
        """Set CC mode current (A)."""
        if not (0 <= amps <= self.MAX_CURRENT_A):
            raise SDL1020XError(f"Current {amps} A out of range (0–{self.MAX_CURRENT_A} A)")
        self.write(f":SOURce:CURRent {amps:.6f}")

    def set_voltage(self, volts: float) -> None:
        """Set CV mode voltage (V)."""
        if not (0 <= volts <= self.MAX_VOLTAGE_V):
            raise SDL1020XError(f"Voltage {volts} V out of range (0–{self.MAX_VOLTAGE_V} V)")
        self.write(f":SOURce:VOLTage {volts:.6f}")

    def set_resistance(self, ohms: float) -> None:
        """Set CR mode resistance (Ω)."""
        if not (0.08 <= ohms <= self.MAX_RESIST_OHM):
            raise SDL1020XError(f"Resistance {ohms} Ω out of range (0.08–{self.MAX_RESIST_OHM} Ω)")
        self.write(f":SOURce:RESistance {ohms:.6f}")

    def set_power(self, watts: float) -> None:
        """Set CP mode power (W)."""
        if not (0 <= watts <= self.MAX_POWER_W):
            raise SDL1020XError(f"Power {watts} W out of range (0–{self.MAX_POWER_W} W)")
        self.write(f":SOURce:POWer {watts:.6f}")

    def get_setpoint(self, mode: Optional[str] = None) -> float:
        """Return the current set-point for the given (or active) mode."""
        if mode is None:
            mode = self.get_mode()
        mode = mode.upper()
        cmd_map = {
            "CC": ":SOURce:CURRent?",
            "CV": ":SOURce:VOLTage?",
            "CR": ":SOURce:RESistance?",
            "CP": ":SOURce:POWer?",
        }
        if mode not in cmd_map:
            raise SDL1020XError(f"Unknown mode '{mode}'")
        return float(self.query(cmd_map[mode]))

    # ------------------------------------------------------------------
    # Dynamic (A/B) levels — two-level CC/CV/CR/CP switching
    # ------------------------------------------------------------------

    def set_level_a(self, value: float) -> None:
        """Set dynamic level A for the current mode."""
        mode = self.get_mode()
        self.write(f":SOURce:{_mode_scpi(mode)}:LEVel:A {value:.6f}")

    def set_level_b(self, value: float) -> None:
        """Set dynamic level B for the current mode."""
        mode = self.get_mode()
        self.write(f":SOURce:{_mode_scpi(mode)}:LEVel:B {value:.6f}")

    # ------------------------------------------------------------------
    # Protection
    # ------------------------------------------------------------------

    def set_ovp(self, volts: float) -> None:
        """Set over-voltage protection threshold (V)."""
        self.write(f":SOURce:VOLTage:PROTection:LEVel {volts:.3f}")

    def set_ocp(self, amps: float) -> None:
        """Set over-current protection threshold (A)."""
        self.write(f":SOURce:CURRent:PROTection:LEVel {amps:.3f}")

    # ------------------------------------------------------------------
    # Measurements
    # ------------------------------------------------------------------

    def measure_voltage(self) -> float:
        """Measure DUT terminal voltage (V)."""
        return float(self.query(":MEASure:VOLTage?"))

    def measure_current(self) -> float:
        """Measure drawn current (A)."""
        return float(self.query(":MEASure:CURRent?"))

    def measure_power(self) -> float:
        """Measure dissipated power (W)."""
        return float(self.query(":MEASure:POWer?"))

    def measure_resistance(self) -> float:
        """Measure terminal resistance (Ω) = V/I."""
        return float(self.query(":MEASure:RESistance?"))

    def measure_all(self) -> dict:
        """Return all four measurements in one call."""
        return {
            "voltage_v":    self.measure_voltage(),
            "current_a":    self.measure_current(),
            "power_w":      self.measure_power(),
            "resistance_ohm": self.measure_resistance(),
        }

    # ------------------------------------------------------------------
    # Timer / battery discharge test
    # ------------------------------------------------------------------

    def set_timer_current(self, amps: float, duration_s: float) -> None:
        """Configure a timed constant-current draw."""
        self.write(f":TIMEr:CURRent {amps:.3f},{duration_s:.3f}")

    def timer_on(self) -> None:
        self.write(":TIMEr:ENABle ON")

    def timer_off(self) -> None:
        self.write(":TIMEr:ENABle OFF")

    def get_timer_elapsed(self) -> float:
        """Return elapsed time in seconds for the active timer test."""
        return float(self.query(":TIMEr:TIME?"))

    # ------------------------------------------------------------------
    # Short circuit test
    # ------------------------------------------------------------------

    def short_on(self) -> None:
        """Activate internal short-circuit test mode."""
        self.write(":SHORt:ENABle ON")

    def short_off(self) -> None:
        self.write(":SHORt:ENABle OFF")

    # ------------------------------------------------------------------
    # System / status
    # ------------------------------------------------------------------

    def get_errors(self) -> str:
        return self.query(":SYSTem:ERRor?").strip()

    def get_protection_status(self) -> dict:
        """Return OVP / OCP / OPP / OTP status flags."""
        raw = self.query(":STATus:QUEStionable:CONDition?")
        try:
            flags = int(raw)
        except ValueError:
            return {"raw": raw}
        return {
            "ovp": bool(flags & (1 << 0)),   # Over Voltage Protection
            "ocp": bool(flags & (1 << 1)),   # Over Current Protection
            "opp": bool(flags & (1 << 2)),   # Over Power Protection
            "otp": bool(flags & (1 << 3)),   # Over Temperature Protection
            "raw_flags": flags,
        }


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _mode_scpi(mode: str) -> str:
    return {"CC": "CURRent", "CV": "VOLTage", "CR": "RESistance", "CP": "POWer"}[mode.upper()]
