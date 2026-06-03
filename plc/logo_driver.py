"""
logo_driver.py
--------------
Modbus TCP driver for the Siemens LOGO! 8 series PLC.

Tested against: LOGO! 12/24RC (6ED1052-1MD08-0BA1) and LOGO! 12/24RCE.

The LOGO! 8 series exposes a Modbus TCP server on port 502. I/O is mapped
to Modbus holding registers and coils using the LOGO!'s fixed memory map.
Enable Modbus server in LOGO! Soft Comfort: Network → Modbus settings.

## LOGO! 8 Modbus Memory Map (default)
+-----------+----------------+---------------------------+
| Type      | Modbus addr    | LOGO! variable            |
+-----------+----------------+---------------------------+
| Coil      | 8192–8199      | Q1–Q8  (digital outputs)  |
| Coil      | 8256–8263      | Q9–Q16 (expansion)        |
| Coil      | 0–7            | M1–M8  (relay markers)    |
| Input     | 8192+          | I1–I8  (digital inputs)   |
| Holding   | 528–531        | V0–V850 (VM memory)       |
+-----------+----------------+---------------------------+

This driver uses the standard Q-coil addresses and V-memory registers.

Dependencies:
    pip install pymodbus>=3.0

Usage:
    plc = LogoDriver()
    plc.connect("192.168.1.100")
    plc.set_output(1, True)    # Energise Q1 (heater relay)
    plc.get_input(1)           # Read I1 (e-stop)
    plc.disconnect()
"""

import threading
import time
from typing import Optional

try:
    from pymodbus.client import ModbusTcpClient
    from pymodbus.exceptions import ModbusException
    PYMODBUS_AVAILABLE = True
except ImportError:
    PYMODBUS_AVAILABLE = False
    ModbusTcpClient = None
    ModbusException = Exception


class LogoError(Exception):
    pass


# ── LOGO! 8 Modbus address constants ──────────────────────────────────────────
# Digital outputs Q1–Q8: coil address base = 8192
Q_COIL_BASE = 8192

# Digital inputs I1–I8: discrete input address base = 8192
I_INPUT_BASE = 8192

# VM (variable memory) holding register base: byte-addressed, word = 2 bytes
VM_REG_BASE = 0   # register address = VM byte offset // 2

# ── Rig-specific I/O assignment ───────────────────────────────────────────────
# These match the wiring guide in docs/wiring-guide.md.
# Adjust if your wiring differs.

OUTPUTS = {
    "HEATER_RELAY":   1,   # Q1 — SSR enable for main heater circuit
    "VENT_RELAY":     2,   # Q2 — 24V power to servo/actuator rail
    "DUT_RELAY":      3,   # Q3 — DUT power enable relay
    "ALARM_OUTPUT":   4,   # Q4 — external alarm beacon / indicator
    "AUX_RELAY_1":    5,   # Q5 — spare
    "AUX_RELAY_2":    6,   # Q6 — spare
    "AUX_RELAY_3":    7,   # Q7 — spare
    "AUX_RELAY_4":    8,   # Q8 — spare
}

INPUTS = {
    "ESTOP":          1,   # I1 — E-stop (NC, HIGH = safe, LOW = tripped)
    "DOOR_INTERLOCK": 2,   # I2 — Chamber door (NC, HIGH = closed)
    "OVERTEMP_CUT":   3,   # I3 — HW overtemp safety cutout (NO thermostat disc)
    "MANUAL_START":   4,   # I4 — Manual run button (NO, momentary)
    "MANUAL_STOP":    5,   # I5 — Manual stop button (NC, momentary)
    "SPARE_I6":       6,
    "SPARE_I7":       7,
    "SPARE_I8":       8,
}


class LogoDriver:
    """
    Thread-safe Modbus TCP driver for the Siemens LOGO! 8 PLC.

    The LOGO! must have:
      - Modbus TCP server enabled (LOGO! Soft Comfort → Network → Modbus)
      - A fixed IP address (set via LOGO! Soft Comfort or DHCP reservation)
      - Port 502 reachable on the local network

    Ladder logic is programmed separately in LOGO! Soft Comfort (see
    docs/plc-ladder-logic.md for the function block specification).
    This driver provides the runtime monitoring and override interface.
    """

    def __init__(self):
        self._lock   = threading.Lock()
        self._client: Optional["ModbusTcpClient"] = None
        self._host   = ""
        self._port   = 502
        self._connected = False

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, host: str, port: int = 502) -> None:
        if not PYMODBUS_AVAILABLE:
            raise LogoError(
                "pymodbus is not installed. Run: pip install pymodbus>=3.0"
            )
        with self._lock:
            self._host = host
            self._port = port
            client = ModbusTcpClient(host, port=port, timeout=5)
            if not client.connect():
                raise LogoError(f"Modbus TCP connect to {host}:{port} failed.")
            self._client = client
            self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            if self._client:
                self._client.close()
                self._client = None
            self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def ping(self) -> bool:
        """Quick liveness check — attempt to read Q1 coil."""
        try:
            self._read_coil(Q_COIL_BASE)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Digital outputs (Q coils)
    # ------------------------------------------------------------------

    def set_output(self, q: int, state: bool) -> None:
        """
        Set digital output Qn (1-based) ON or OFF.
        Writes to the LOGO! Q coil address: Q_COIL_BASE + (q - 1).
        """
        self._validate_q(q)
        addr = Q_COIL_BASE + (q - 1)
        with self._lock:
            result = self._client.write_coil(addr, state)
            if result.isError():
                raise LogoError(f"Write coil Q{q} failed: {result}")

    def get_output(self, q: int) -> bool:
        """Read the current state of digital output Qn."""
        self._validate_q(q)
        addr = Q_COIL_BASE + (q - 1)
        return self._read_coil(addr)

    def set_named_output(self, name: str, state: bool) -> None:
        """Set an output by name (from OUTPUTS dict)."""
        if name not in OUTPUTS:
            raise LogoError(f"Unknown output name '{name}'. Valid: {list(OUTPUTS)}")
        self.set_output(OUTPUTS[name], state)

    def get_named_output(self, name: str) -> bool:
        if name not in OUTPUTS:
            raise LogoError(f"Unknown output name '{name}'")
        return self.get_output(OUTPUTS[name])

    def get_all_outputs(self) -> dict:
        """Return dict of all Q output states."""
        return {
            name: self.get_output(q)
            for name, q in OUTPUTS.items()
        }

    # ------------------------------------------------------------------
    # Digital inputs (I discrete inputs)
    # ------------------------------------------------------------------

    def get_input(self, i: int) -> bool:
        """Read digital input In (1-based)."""
        self._validate_i(i)
        addr = I_INPUT_BASE + (i - 1)
        with self._lock:
            result = self._client.read_discrete_inputs(addr, count=1)
            if result.isError():
                raise LogoError(f"Read input I{i} failed: {result}")
            return bool(result.bits[0])

    def get_named_input(self, name: str) -> bool:
        if name not in INPUTS:
            raise LogoError(f"Unknown input name '{name}'. Valid: {list(INPUTS)}")
        return self.get_input(INPUTS[name])

    def get_all_inputs(self) -> dict:
        return {
            name: self.get_input(i)
            for name, i in INPUTS.items()
        }

    # ------------------------------------------------------------------
    # Safety checks (convenience wrappers)
    # ------------------------------------------------------------------

    def is_safe_to_run(self) -> tuple[bool, list[str]]:
        """
        Return (safe: bool, reasons: list[str]).
        All conditions must be True to permit a test run.
        """
        faults = []
        try:
            if not self.get_named_input("ESTOP"):
                faults.append("E-stop is TRIPPED (I1 LOW)")
            if not self.get_named_input("DOOR_INTERLOCK"):
                faults.append("Chamber door is OPEN (I2 LOW)")
            if self.get_named_input("OVERTEMP_CUT"):
                faults.append("Hardware overtemp cutout is ACTIVE (I3 HIGH)")
        except LogoError as e:
            faults.append(f"PLC communication error: {e}")
        return (len(faults) == 0, faults)

    def emergency_stop(self) -> None:
        """Immediately de-energise all LOGO! outputs."""
        for name in OUTPUTS:
            try:
                self.set_named_output(name, False)
            except LogoError:
                pass

    # ------------------------------------------------------------------
    # VM (variable memory) — for passing set-points to ladder logic
    # ------------------------------------------------------------------

    def write_vm_word(self, byte_offset: int, value: int) -> None:
        """
        Write a 16-bit unsigned integer to LOGO! VM at byte_offset.
        byte_offset must be even (word-aligned).
        """
        if byte_offset % 2 != 0:
            raise LogoError("VM byte_offset must be even (word-aligned).")
        reg = VM_REG_BASE + byte_offset // 2
        with self._lock:
            result = self._client.write_register(reg, value & 0xFFFF)
            if result.isError():
                raise LogoError(f"Write VM[{byte_offset}] failed: {result}")

    def read_vm_word(self, byte_offset: int) -> int:
        if byte_offset % 2 != 0:
            raise LogoError("VM byte_offset must be even (word-aligned).")
        reg = VM_REG_BASE + byte_offset // 2
        with self._lock:
            result = self._client.read_holding_registers(reg, count=1)
            if result.isError():
                raise LogoError(f"Read VM[{byte_offset}] failed: {result}")
            return result.registers[0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_coil(self, addr: int) -> bool:
        with self._lock:
            result = self._client.read_coils(addr, count=1)
            if result.isError():
                raise LogoError(f"Read coil @{addr} failed: {result}")
            return bool(result.bits[0])

    @staticmethod
    def _validate_q(q: int) -> None:
        if not (1 <= q <= 8):
            raise LogoError(f"Q output index {q} out of range (1–8)")

    @staticmethod
    def _validate_i(i: int) -> None:
        if not (1 <= i <= 8):
            raise LogoError(f"I input index {i} out of range (1–8)")
