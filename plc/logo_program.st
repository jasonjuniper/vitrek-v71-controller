(*
================================================================================
  Juniper Test Station — Rig Control Program
  IEC 61131-3 Structured Text (ST) Reference Implementation
================================================================================

  This file is the open-standard reference for the rig control logic.
  It can be loaded into OpenPLC Runtime (https://openplcproject.com) running
  on a Raspberry Pi if you want a soft-PLC alternative to LOGO! Soft Comfort.

  For the Siemens LOGO! 12/24RCE, translate this logic into LOGO! Soft Comfort
  FBD networks using the step-by-step guide in:
    docs/logo-soft-comfort-guide.md

  Variable mapping to LOGO! hardware:
  ────────────────────────────────────────────────────────────────────────────
  ST variable   LOGO! terminal  Modbus address   Description
  ────────────────────────────────────────────────────────────────────────────
  I1            I1              Discrete 8192    E-stop (NC — HIGH = safe)
  I2            I2              Discrete 8193    Door interlock (NC — HIGH = closed)
  I3            I3              Discrete 8194    HW overtemp cutout (NO — HIGH = tripped)
  I4            I4              Discrete 8195    Manual start button (NO, momentary)
  I5            I5              Discrete 8196    Manual stop button (NC — HIGH = safe)
  Q1            Q1              Coil 8192        Heater SSR relay
  Q2            Q2              Coil 8193        Servo 24V power relay
  Q3            Q3              Coil 8194        DUT power relay
  Q4            Q4              Coil 8195        Alarm beacon
  SW_HEATER     M3              Coil 2           Software heater enable (from RPi)
  SW_SERVO      M4              Coil 3           Software servo-power enable (from RPi)
  SW_DUT        M5              Coil 4           Software DUT-power enable (from RPi)
  ────────────────────────────────────────────────────────────────────────────

  Safety chain summary:
    Q1 (heater)  = SafetyGate AND RunLatch AND SW_HEATER
    Q2 (servos)  = SafetyGate AND SW_SERVO       (* servos may move when idle *)
    Q3 (DUT pwr) = SafetyGate AND RunLatch AND SW_DUT
    Q4 (alarm)   = fires on ANY safety fault, independent of software state

================================================================================
*)

PROGRAM Rig_Control

VAR
    (* ── Physical inputs ──────────────────────────────────────────────── *)
    I1  : BOOL;   (* E-stop mushroom button,       NC — HIGH = safe        *)
    I2  : BOOL;   (* Chamber door reed switch,     NC — HIGH = closed      *)
    I3  : BOOL;   (* HW bimetallic overtemp disc,  NO — HIGH = tripped     *)
    I4  : BOOL;   (* Manual start button,          NO — HIGH = pressed     *)
    I5  : BOOL;   (* Manual stop button,           NC — HIGH = safe        *)

    (* ── Physical outputs ─────────────────────────────────────────────── *)
    Q1  : BOOL;   (* Heater SSR relay                                      *)
    Q2  : BOOL;   (* Servo 24V power rail relay                            *)
    Q3  : BOOL;   (* DUT power relay                                       *)
    Q4  : BOOL;   (* Alarm beacon                                          *)

    (* ── Software enables from RPi via Modbus M markers ─────────────────*)
    SW_HEATER : BOOL;   (* M3 — RPi requests heater ON                    *)
    SW_SERVO  : BOOL;   (* M4 — RPi requests servo power ON               *)
    SW_DUT    : BOOL;   (* M5 — RPi requests DUT power ON                 *)

    (* ── Internal flags ───────────────────────────────────────────────── *)
    SafetyGate : BOOL;  (* M1 — all hardware safety conditions met         *)
    RunLatch   : BOOL;  (* M2 — run sequence latched (SR flip-flop output) *)

    (* ── SR flip-flop state variable ──────────────────────────────────── *)
    RunLatch_FF : SR;
END_VAR

(* ════════════════════════════════════════════════════════════════════════════
   NETWORK 1 — Safety gate (M1 / SafetyGate)

   SafetyGate is HIGH only when ALL physical safety conditions are met:
     I1 HIGH  = E-stop released (NC contact, switch closed = circuit made)
     I2 HIGH  = Door closed     (NC contact, door shut = circuit made)
     NOT I3   = Overtemp OK     (NO contact, under 120 °C = switch open = I3 LOW)

   SafetyGate feeds every output rung.  Any hardware fault instantly takes
   SafetyGate LOW, de-energising Q1 and Q3 within one PLC scan cycle.
   ════════════════════════════════════════════════════════════════════════════ *)
SafetyGate := I1 AND I2 AND NOT I3;


(* ════════════════════════════════════════════════════════════════════════════
   NETWORK 2 — Run latch (M2 / RunLatch)

   SR flip-flop:
     SET  = Manual start button (I4) momentary press
     RESET= Manual stop button NOT (I5 NC contact, open = fault = LOW)
          OR SafetyGate lost (any hardware fault)

   The RPi can also act as a software start by momentarily writing SW_HEATER
   and SW_DUT; but the latch must be set first.  An alternative is to add a
   software-start VM bit if unattended starts are needed.
   ════════════════════════════════════════════════════════════════════════════ *)
RunLatch_FF(S1 := I4,
            R  := (NOT I5) OR (NOT SafetyGate));
RunLatch := RunLatch_FF.Q1;


(* ════════════════════════════════════════════════════════════════════════════
   NETWORK 3 — Q1 Heater relay

   Heater energises ONLY when:
     1. SafetyGate is HIGH (all hardware safety OK)
     2. RunLatch is HIGH   (sequence has been started)
     3. SW_HEATER is HIGH  (RPi software has requested heater ON)

   The heater is the highest-risk output; it requires all three conditions.
   ════════════════════════════════════════════════════════════════════════════ *)
Q1 := SafetyGate AND RunLatch AND SW_HEATER;


(* ════════════════════════════════════════════════════════════════════════════
   NETWORK 4 — Q2 Servo power relay

   Servo power requires ONLY the safety gate (not the run latch) so that
   vents can be positioned to their home / closed position at any time —
   including during idle or cool-down, before a run is initiated.
   ════════════════════════════════════════════════════════════════════════════ *)
Q2 := SafetyGate AND SW_SERVO;


(* ════════════════════════════════════════════════════════════════════════════
   NETWORK 5 — Q3 DUT power relay

   DUT power follows the same three-condition pattern as the heater.
   Never energise the DUT while a hardware fault is active.
   ════════════════════════════════════════════════════════════════════════════ *)
Q3 := SafetyGate AND RunLatch AND SW_DUT;


(* ════════════════════════════════════════════════════════════════════════════
   NETWORK 6 — Q4 Alarm beacon

   The alarm fires on ANY safety fault, independently of all software state.
   It uses the INVERSE of the safety conditions so that it fires the moment
   a fault appears, even if the rest of the system hasn't reacted yet.

     NOT I1 = E-stop tripped (NC contact opened = I1 LOW)
     NOT I2 = Door open      (NC contact opened = I2 LOW)
     I3     = Overtemp active (NO contact closed = I3 HIGH)

   Note: Q4 cannot be silenced by writing SW_ENABLES.  Clear the hardware
   fault condition to extinguish the alarm.
   ════════════════════════════════════════════════════════════════════════════ *)
Q4 := (NOT I1) OR (NOT I2) OR I3;


END_PROGRAM
