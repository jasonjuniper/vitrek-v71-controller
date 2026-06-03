"""
thermal_controller.py
---------------------
Thermal test rig controller for the Juniper automated test station.

Manages:
  - 4× K-type thermocouples via MAX31855 SPI amplifiers
  - PWM-controlled heater via GPIO → SSR (solid-state relay)
  - 2× servo motors for vent valve control via GPIO PWM
  - LOGO! PLC safety interlocks (via logo_driver.py)
  - PID temperature loop (software, on the HMI host)

## Transport modes (set in plc/rig_config.json → "transport")

  "gpio"   Raspberry Pi 4/5 — direct GPIO/SPI. Requires RPi.GPIO, spidev,
           adafruit-circuitpython-max31855. On RPi 5 also install rpi-lgpio
           as a drop-in RPi.GPIO shim: pip install rpi-lgpio.

  "http"   WeMos D1 Mini (ESP8266) thermal bridge — no RPi required.
           Hardware I/O runs on the ESP8266 running esp8266/thermal-bridge/.
           The NUC runs Flask + Modbus TCP. The bridge exposes a simple HTTP
           API that this controller calls for sensor reads and actuator commands.
           Set "esp8266.host" and "esp8266.port" in rig_config.json.

  "mock"   Windows / dev machine — fully stubbed with drifting mock temperatures.
           Automatically selected if RPi.GPIO is not importable (i.e. on Windows).

## RPi wiring (see docs/wiring-guide.md for full detail)
  SPI0:
    CE0 (GPIO8)  → MAX31855 #1 CS  (TC1 — ambient)
    CE1 (GPIO7)  → MAX31855 #2 CS  (TC2 — DUT surface)
    GPIO25       → MAX31855 #3 CS  (TC3 — heater element)
    GPIO24       → MAX31855 #4 CS  (TC4 — exhaust/vent)
    SCLK (GPIO11), MISO (GPIO9) shared

  PWM:
    GPIO12  → SSR signal input → heater circuit
    GPIO13  → Servo 1 PWM (vent valve A)
    GPIO18  → Servo 2 PWM (vent valve B)

## ESP8266 bridge wiring (see docs/wiring-guide.md)
  D5 GPIO14 CLK, D6 GPIO12 MISO
  D1 GPIO5 CS-TC1, D2 GPIO4 CS-TC2, D7 GPIO13 CS-TC3
  D8 GPIO15 SSR PWM, D3 GPIO0 Servo A, D4 GPIO2 Servo B

Dependencies (RPi gpio mode):
    pip install RPi.GPIO spidev adafruit-circuitpython-max31855
    # RPi 5: pip install rpi-lgpio adafruit-circuitpython-max31855

Dependencies (http mode — NUC/Windows):
    pip install requests    (standard, usually already installed)

Dependencies (mock mode):
    None (pure Python stubs)
"""

import json
import threading
import time
import math
from typing import Optional

# Try to import RPi GPIO — fall back gracefully on non-RPi platforms
try:
    import RPi.GPIO as GPIO
    import spidev
    import adafruit_max31855
    import busio
    import digitalio
    import board
    RPI_AVAILABLE = True
except ImportError:
    GPIO = None
    RPI_AVAILABLE = False

# HTTP client for ESP8266 bridge transport
try:
    import requests as _requests
    REQUESTS_AVAILABLE = True
except ImportError:
    _requests = None
    REQUESTS_AVAILABLE = False


# ── Hardware constants ─────────────────────────────────────────────────────────

# GPIO BCM pin numbers
PIN_SSR_PWM   = 12    # Heater SSR — hardware PWM channel 0
PIN_SERVO_A   = 13    # Vent valve A — hardware PWM channel 1
PIN_SERVO_B   = 18    # Vent valve B — hardware PWM channel 0 alt
PIN_TC1_CS    = 8     # MAX31855 #1 chip-select (SPI CE0)
PIN_TC2_CS    = 7     # MAX31855 #2 chip-select (SPI CE1)
PIN_TC3_CS    = 25    # MAX31855 #3 chip-select (software CS)
PIN_TC4_CS    = 24    # MAX31855 #4 chip-select (software CS)

# PWM
HEATER_PWM_HZ    = 10    # 10 Hz for SSR — most SSRs switch at ≥10 Hz fine
SERVO_PWM_HZ     = 50    # Standard 50 Hz servo PWM

# Servo pulse widths in microseconds
SERVO_MIN_US  = 500     # 0.5 ms → 0° (fully closed)
SERVO_MAX_US  = 2500    # 2.5 ms → 180° (fully open)

# Safety limits
MAX_TEMP_C        = 120.0  # Absolute max allowable temperature
HEATER_MAX_DUTY   = 90.0   # % — cap heater duty to protect SSR
OVERSHOOT_MARGIN  = 5.0    # °C above setpoint to trigger "close enough" flag


# ── Thermocouple channel names ─────────────────────────────────────────────────

TC_CHANNELS = {
    "TC1_AMBIENT":  {"cs_pin": PIN_TC1_CS, "desc": "Ambient / chamber air"},
    "TC2_DUT":      {"cs_pin": PIN_TC2_CS, "desc": "DUT surface"},
    "TC3_HEATER":   {"cs_pin": PIN_TC3_CS, "desc": "Heater element"},
    "TC4_EXHAUST":  {"cs_pin": PIN_TC4_CS, "desc": "Exhaust / vent outlet"},
}


# ── Mock layer (non-RPi platforms) ─────────────────────────────────────────────

class _MockTC:
    """Simulated thermocouple — returns a slowly drifting temperature."""
    def __init__(self, base_temp=22.0):
        self._t = base_temp
        self._drift = 0.01

    @property
    def temperature(self) -> float:
        import random
        self._t += self._drift + random.uniform(-0.05, 0.05)
        return round(self._t, 2)

    def set_sim_temp(self, t: float):
        self._t = t


class _MockGPIO:
    """Stub GPIO for non-RPi development."""
    BCM = "BCM"
    OUT = "OUT"
    IN  = "IN"
    _state = {}

    @classmethod
    def setmode(cls, *a): pass
    @classmethod
    def setup(cls, pin, mode, **kw): pass
    @classmethod
    def output(cls, pin, val): cls._state[pin] = val
    @classmethod
    def input(cls, pin): return cls._state.get(pin, False)
    @classmethod
    def cleanup(cls): pass

    class PWM:
        def __init__(self, pin, freq): self._duty = 0.0
        def start(self, duty): self._duty = duty
        def ChangeDutyCycle(self, duty): self._duty = duty
        def ChangeFrequency(self, freq): pass
        def stop(self): self._duty = 0.0


# ── PID controller ─────────────────────────────────────────────────────────────

class PIDController:
    """
    Simple software PID loop for heater temperature control.

    Tuning guidance for a resistive heater in a small chamber:
      Kp = 5.0   (proportional — respond to error)
      Ki = 0.1   (integral — eliminate steady-state offset)
      Kd = 1.0   (derivative — dampen overshoot)

    These values are starting points. Tune with a step-response test:
      1. Set setpoint 10°C above ambient.
      2. Run PID, log temperature.
      3. Adjust Kp until fast response without oscillation.
      4. Add Ki to eliminate steady-state error.
      5. Add Kd to reduce overshoot.
    """

    def __init__(self, kp: float = 5.0, ki: float = 0.1, kd: float = 1.0,
                 output_min: float = 0.0, output_max: float = 100.0):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_min = output_min
        self.output_max = output_max
        self._integral   = 0.0
        self._last_error = 0.0
        self._last_time  = time.monotonic()

    def reset(self):
        self._integral   = 0.0
        self._last_error = 0.0
        self._last_time  = time.monotonic()

    def compute(self, setpoint: float, measurement: float) -> float:
        now  = time.monotonic()
        dt   = now - self._last_time
        if dt <= 0:
            return self.output_min
        error = setpoint - measurement
        self._integral  += error * dt
        derivative = (error - self._last_error) / dt
        output = self.kp * error + self.ki * self._integral + self.kd * derivative
        output = max(self.output_min, min(self.output_max, output))
        self._last_error = error
        self._last_time  = now
        return output


# ── Thermal controller ─────────────────────────────────────────────────────────

class ThermalController:
    """
    High-level thermal test rig controller.

    Manages heater PID loop, thermocouple reads, servo vent positions,
    and safety interlocks.  Runs a background thread when a soak profile
    is active.

    Example:
        tc = ThermalController()
        tc.init()
        tc.set_setpoint(85.0)           # target 85°C
        tc.set_vent_position("A", 50)   # vent A 50% open
        tc.start_control_loop()
        time.sleep(300)                 # 5 minute soak
        print(tc.read_all_temps())
        tc.stop_control_loop()
        tc.shutdown()
    """

    def __init__(self, plc=None, transport: str = "auto",
                 esp_host: str = "192.168.1.60", esp_port: int = 80):
        """
        Parameters
        ----------
        plc : LogoDriver | None
            Optional LOGO! PLC driver for safety interlock reads.
        transport : str
            "auto"  — use "gpio" if RPi.GPIO available, else "mock"
            "gpio"  — Raspberry Pi GPIO/SPI direct (RPi 4 or 5 + rpi-lgpio)
            "http"  — ESP8266 thermal bridge HTTP API
            "mock"  — fully stubbed simulation (dev/Windows)
        esp_host : str
            IP address of the ESP8266 bridge (transport="http" only).
        esp_port : int
            HTTP port of the bridge (default 80).
        """
        self._plc   = plc
        self._lock  = threading.Lock()
        self._gpio  = None
        self._tc_sensors = {}
        self._heater_pwm = None
        self._servo_a_pwm = None
        self._servo_b_pwm = None
        self._pid   = PIDController()
        self._setpoint_c = 25.0
        self._running    = False
        self._thread: Optional[threading.Thread] = None

        # Resolve transport
        if transport == "auto":
            transport = "gpio" if RPI_AVAILABLE else "mock"
        self._transport = transport
        self._esp_base  = f"http://{esp_host}:{esp_port}"

        # Telemetry (updated by control loop)
        self.temps: dict[str, Optional[float]] = {k: None for k in TC_CHANNELS}
        self.heater_duty = 0.0
        self.vent_position = {"A": 0.0, "B": 0.0}   # 0–100 %
        self.control_active = False
        self.fault_message: Optional[str] = None

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Set up hardware transport (GPIO, HTTP bridge, or mock)."""
        if self._transport == "gpio":
            import board, busio, digitalio, adafruit_max31855
            spi = busio.SPI(board.SCK, MISO=board.MISO)
            cs_pins = {
                "TC1_AMBIENT": board.CE0,
                "TC2_DUT":     board.CE1,
                "TC3_HEATER":  getattr(board, "D25", None),
                "TC4_EXHAUST": getattr(board, "D24", None),
            }
            for name, cs_pin in cs_pins.items():
                if cs_pin:
                    cs = digitalio.DigitalInOut(cs_pin)
                    self._tc_sensors[name] = adafruit_max31855.MAX31855(spi, cs)

            GPIO.setmode(GPIO.BCM)
            GPIO.setup(PIN_SSR_PWM, GPIO.OUT)
            GPIO.setup(PIN_SERVO_A, GPIO.OUT)
            GPIO.setup(PIN_SERVO_B, GPIO.OUT)
            self._heater_pwm  = GPIO.PWM(PIN_SSR_PWM, HEATER_PWM_HZ)
            self._servo_a_pwm = GPIO.PWM(PIN_SERVO_A, SERVO_PWM_HZ)
            self._servo_b_pwm = GPIO.PWM(PIN_SERVO_B, SERVO_PWM_HZ)
            self._heater_pwm.start(0)
            self._servo_a_pwm.start(0)
            self._servo_b_pwm.start(0)
            self._gpio = GPIO

        elif self._transport == "http":
            if not REQUESTS_AVAILABLE:
                raise RuntimeError(
                    "transport='http' requires the 'requests' library.\n"
                    "Install with: pip install requests --break-system-packages"
                )
            # No local GPIO — verify the bridge is reachable
            try:
                r = _requests.get(f"{self._esp_base}/status", timeout=5)
                r.raise_for_status()
            except Exception as e:
                raise RuntimeError(
                    f"Cannot reach ESP8266 thermal bridge at {self._esp_base}: {e}\n"
                    "Check that the D1 Mini is powered, connected to WiFi, and "
                    "secrets.h has the correct IP."
                )
            # Stub sensors — reads go to HTTP
            for name in TC_CHANNELS:
                self._tc_sensors[name] = None

        else:
            # Mock mode — stub sensors for dev/Windows
            for name in TC_CHANNELS:
                self._tc_sensors[name] = _MockTC(base_temp=22.0 + list(TC_CHANNELS).index(name) * 2)
            mock = _MockGPIO
            self._heater_pwm  = mock.PWM(PIN_SSR_PWM, HEATER_PWM_HZ)
            self._servo_a_pwm = mock.PWM(PIN_SERVO_A, SERVO_PWM_HZ)
            self._servo_b_pwm = mock.PWM(PIN_SERVO_B, SERVO_PWM_HZ)
            self._heater_pwm.start(0)
            self._servo_a_pwm.start(0)
            self._servo_b_pwm.start(0)
            self._gpio = mock

    def shutdown(self) -> None:
        self.stop_control_loop()
        self._set_heater_duty(0)
        self._set_servo_angle("A", 0)
        self._set_servo_angle("B", 0)
        if self._gpio and RPI_AVAILABLE:
            self._gpio.cleanup()

    # ------------------------------------------------------------------
    # Temperature
    # ------------------------------------------------------------------

    def read_temp(self, channel: str) -> Optional[float]:
        """Read one thermocouple channel. Returns None on sensor fault."""
        if self._transport == "http":
            # Batch fetch all temps and cache; return the requested channel
            try:
                r = _requests.get(f"{self._esp_base}/sensors", timeout=3)
                data = r.json()
                key_map = {
                    "TC1_AMBIENT": "tc1_c",
                    "TC2_DUT":     "tc2_c",
                    "TC3_HEATER":  "tc3_c",
                    "TC4_EXHAUST": "tc4_c",
                }
                return data.get(key_map.get(channel))
            except Exception:
                return None

        sensor = self._tc_sensors.get(channel)
        if not sensor:
            return None
        try:
            return sensor.temperature
        except Exception:
            return None

    def read_all_temps(self) -> dict:
        if self._transport == "http":
            try:
                r = _requests.get(f"{self._esp_base}/sensors", timeout=3)
                data = r.json()
                return {
                    "TC1_AMBIENT": data.get("tc1_c"),
                    "TC2_DUT":     data.get("tc2_c"),
                    "TC3_HEATER":  data.get("tc3_c"),
                    "TC4_EXHAUST": data.get("tc4_c"),
                }
            except Exception:
                return {k: None for k in TC_CHANNELS}
        return {name: self.read_temp(name) for name in TC_CHANNELS}

    def set_setpoint(self, temp_c: float) -> None:
        """Set heater target temperature (°C)."""
        if temp_c > MAX_TEMP_C:
            raise ValueError(f"Setpoint {temp_c}°C exceeds safety limit {MAX_TEMP_C}°C")
        with self._lock:
            self._setpoint_c = temp_c
            self._pid.reset()

    def get_setpoint(self) -> float:
        return self._setpoint_c

    # ------------------------------------------------------------------
    # Heater PWM
    # ------------------------------------------------------------------

    def _set_heater_duty(self, duty: float) -> None:
        """Set heater duty cycle (0–100 %). Capped at HEATER_MAX_DUTY."""
        duty = max(0.0, min(HEATER_MAX_DUTY, duty))
        self.heater_duty = duty
        if self._transport == "http":
            try:
                _requests.post(f"{self._esp_base}/heater",
                               json={"duty": int(duty)}, timeout=3)
            except Exception:
                pass
        elif self._heater_pwm:
            self._heater_pwm.ChangeDutyCycle(duty)
        if self._plc:
            try:
                self._plc.set_named_output("HEATER_RELAY", duty > 0)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Servo / vent control
    # ------------------------------------------------------------------

    def set_vent_position(self, vent: str, percent: float) -> None:
        """
        Set vent valve position.
        vent: 'A' or 'B'
        percent: 0.0 (closed) to 100.0 (fully open)
        """
        percent = max(0.0, min(100.0, percent))
        self.vent_position[vent] = percent
        self._set_servo_angle(vent, percent / 100.0 * 180.0)

    def _set_servo_angle(self, vent: str, angle_deg: float) -> None:
        """Convert angle (0–180°) to servo PWM duty cycle."""
        if self._transport == "http":
            pct = int(angle_deg / 180.0 * 100)
            payload = {"a": pct} if vent == "A" else {"b": pct}
            try:
                _requests.post(f"{self._esp_base}/servo",
                               json=payload, timeout=3)
            except Exception:
                pass
            return
        pulse_us = SERVO_MIN_US + (SERVO_MAX_US - SERVO_MIN_US) * angle_deg / 180.0
        duty_pct = pulse_us / (1_000_000.0 / SERVO_PWM_HZ) * 100.0
        pwm = self._servo_a_pwm if vent == "A" else self._servo_b_pwm
        if pwm:
            pwm.ChangeDutyCycle(duty_pct)

    # ------------------------------------------------------------------
    # PID control loop
    # ------------------------------------------------------------------

    def start_control_loop(self, interval_s: float = 1.0) -> None:
        """Start the background PID heater control thread."""
        if self._running:
            return
        self._running = True
        self.control_active = True
        self._pid.reset()
        self._thread = threading.Thread(
            target=self._control_loop, args=(interval_s,), daemon=True
        )
        self._thread.start()

    def stop_control_loop(self) -> None:
        self._running = False
        self.control_active = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._set_heater_duty(0)

    def _control_loop(self, interval_s: float) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as e:
                self.fault_message = str(e)
            time.sleep(interval_s)

    def _tick(self) -> None:
        """One PID iteration — read DUT temp, compute duty, apply heater."""
        # Safety interlock check via LOGO! PLC
        if self._plc and self._plc.connected:
            safe, reasons = self._plc.is_safe_to_run()
            if not safe:
                self._set_heater_duty(0)
                self.fault_message = "; ".join(reasons)
                return

        # Read all temps and update telemetry
        for name in TC_CHANNELS:
            self.temps[name] = self.read_temp(name)

        # Use TC2_DUT as the control feedback sensor
        feedback = self.temps.get("TC2_DUT")
        if feedback is None:
            self._set_heater_duty(0)
            self.fault_message = "TC2_DUT read failure — heater disabled"
            return

        # Hard overtemp safety cutout (redundant with LOGO! HW cutout)
        if feedback >= MAX_TEMP_C:
            self._set_heater_duty(0)
            self.fault_message = f"OVERTEMP: {feedback:.1f}°C ≥ {MAX_TEMP_C}°C — heater disabled"
            return

        self.fault_message = None
        duty = self._pid.compute(self._setpoint_c, feedback)
        self._set_heater_duty(duty)

    # ------------------------------------------------------------------
    # Status snapshot
    # ------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "temps":           dict(self.temps),
            "setpoint_c":      self._setpoint_c,
            "heater_duty_pct": self.heater_duty,
            "vent_position":   dict(self.vent_position),
            "control_active":  self.control_active,
            "fault":           self.fault_message,
            "transport":       self._transport,
        }
