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

## Safety Architecture
Software output control goes through LOGO! marker flags (M markers), NOT
directly to Q output coils.  The LOGO! ladder program ANDs each M-marker
"software enable" with hardware safety conditions (I1/I2/I3) before
energising the physical relay.  This means a fault on I1 (E-stop), I2
(door interlock), or I3 (overtemp cutout) de-energises Q1/Q3 immediately
at the PLC — even if the RPi software hangs and never clears its M bit.

  M3 (coil 2) = SW_HEATER_ENABLE  →  Q1 = M1_safe AND M2_run AND M3
  M4 (coil 3) = SW_SERVO_ENABLE   →  Q2 = M1_safe AND M4
  M5 (coil 4) = SW_DUT_ENABLE     →  Q3 = M1_safe AND M2_run AND M5
  Q4 (alarm) is driven purely by the ladder from I1/I2/I3 faults.

M1 and M2 are internal ladder flags; they are computed by the LOGO! FBD
program and are NOT written by this driver.

Dependencies:
    pip install pymodbus>=3.0

Usage:
    plc = LogoDriver()
    plc.connect("192.168.1.100")
    plc.set_named_output("HEATER_RELAY", True)   # request heater ON
    plc.get_input(1)                              # read I1 (e-stop)
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
# Digital outputs Q1–Q8: coil address base = 8192  (READ only from this driver)
Q_COIL_BASE = 8192

# Digital inputs I1–I8: discrete input address base = 8192
I_INPUT_BASE = 8192

# Marker flags M1–M8: coil addresses 0–7
M_COIL_BASE = 0

# VM (variable memory) holding register base: byte-addressed, word = 2 bytes
VM_REG_BASE = 0   # register address = VM byte offset // 2

# ── Rig-specific I/O assignment ───────────────────────────────────────────────
# These match the wiring guide in docs/wiring-guide.md.
# Adjust if your wiring differs.

# Software enable M markers written by this driver (coil = M_COIL_BASE + n-1).
# The ladder ANDs these with hardware safety before driving the Q relay.
# M1 and M2 are reserved for internal ladder use (safety gate and run latch).
SW_ENABLES = {
    "HEATER_RELAY":   3,   # M3 (coil 2) → Q1 controlled by ladder
    "VENT_RELAY":     4,   # M4 (coil 3) → Q2 controlled by ladder
    "DUT_RELAY":      5,   # M5 (coil 4) → Q3 controlled by ladder
    # Q4 (ALARM) is driven directly by the ladder — not writable from here.
}

# Actual physical Q output coil addresses (used for READ-BACK only).
Q_OUTPUTS = {
    "HEATER_RELAY":   1,   # Q1
    "VENT_RELAY":     2,   # Q2
    "DUT_RELAY":      3,   # Q3
    "ALARM_OUTPUT":   4,   # Q4 — ladder-driven, read-back only
    "AUX_RELAY_1":    5,   # Q5
    "AUX_RELAY_2":    6,   # Q6
    "AUX_RELAY_3":    7,   # Q7
    "AUX_RELAY_4":    8,   # Q8
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
    # Software enables (M marker coils — safety-gated by ladder)
    # ------------------------------------------------------------------

    def set_named_output(self, name: str, state: bool) -> None:
        """
        Request an output ON or OFF by writing the matching M-marker coil.
        The LOGO! ladder enforces hardware safety (I1/I2/I3) before the
        physical relay actually energises — so this call does NOT bypass
        the E-stop or door interlock.
        """
        if name not in SW_ENABLES:
            raise LogoError(
                f"Unknown controllable output '{name}'. "
                f"Valid: {list(SW_ENABLES)}. "
                f"Note: ALARM_OUTPUT is ladder-only."
            )
        m_num = SW_ENABLES[name]
        addr = M_COIL_BASE + (m_num - 1)
        with self._lock:
            result = self._client.write_coil(addr, state)
            if result.isError():
                raise LogoError(f"Write M{m_num} (SW enable for {name}) failed: {result}")

    def get_sw_enable(self, name: str) -> bool:
        """Read the software-enable M-marker state (requested state, not actual relay)."""
        if name not in SW_ENABLES:
            raise LogoError(f"Unknown output name '{name}'")
        m_num = SW_ENABLES[name]
        return self._read_coil(M_COIL_BASE + (m_num - 1))

    def get_named_output(self, name: str) -> bool:
        """Read the ACTUAL relay output state (Q coil read-back from LOGO!)."""
        if name not in Q_OUTPUTS:
            raise LogoError(f"Unknown output name '{name}'. Valid: {list(Q_OUTPUTS)}")
        addr = Q_COIL_BASE + (Q_OUTPUTS[name] - 1)
        return self._read_coil(addr)

    def get_all_outputs(self) -> dict:
        """Return dict of all physical Q relay output states (read-back)."""
        return {
            name: self._read_coil(Q_COIL_BASE + (q - 1))
            for name, q in Q_OUTPUTS.items()
        }

    def get_all_sw_enables(self) -> dict:
        """Return dict of all M-marker software enable states."""
        return {
            name: self._read_coil(M_COIL_BASE + (m - 1))
            for name, m in SW_ENABLES.items()
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
        """
        Clear all software-enable M markers.
        The ladder will then de-energise Q1/Q2/Q3.  Q4 (alarm) is driven by
        hardware fault conditions in the ladder — clearing SW enables does
        not silence it.  The hardware E-stop (I1) independently cuts all
        outputs at the LOGO! regardless of software state.
        """
        for name in SW_ENABLES:
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
