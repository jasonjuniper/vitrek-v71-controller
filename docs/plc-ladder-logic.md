<p align="center"><img src="../assets/juniper-banner.svg" alt="JUNIPER · Lighting · Power Solutions · Systems" width="900"></p>

# Siemens LOGO! PLC — Ladder Logic Specification

> 📄 **Print-ready PDF:** [`docs/pdf/plc-ladder-logic.pdf`](pdf/plc-ladder-logic.pdf)

This document specifies all function blocks for the LOGO! Soft Comfort program. Program the LOGO! from this spec before connecting the rig to the RPi HMI.

---

## LOGO! Model

**LOGO! 12/24RCE** (6ED1052-1MD08-0BA1)
- Supply: 12/24V DC
- Inputs: 8 digital (24V DC), 4 analog
- Outputs: 4 relay (250V AC, 10A each)
- Expansion: up to 24 I/O via expansion modules
- Ethernet: built-in for LOGO! Web Server + Modbus TCP (port 502)
- Software: LOGO! Soft Comfort v8.x (included on CD)

---

## I/O Assignment

| Terminal | Type   | Signal name       | Description                             | Switch type |
|----------|--------|-------------------|-----------------------------------------|-------------|
| I1       | Input  | ESTOP             | E-stop mushroom button                  | NC          |
| I2       | Input  | DOOR_INTERLOCK    | Chamber door reed switch                | NC          |
| I3       | Input  | OVERTEMP_HW       | 120°C bimetallic thermostat cutout      | NO          |
| I4       | Input  | MANUAL_START      | Green momentary start button            | NO          |
| I5       | Input  | MANUAL_STOP       | Red momentary stop button               | NC          |
| I6–I8    | Input  | SPARE             | Reserved for future expansion           | —           |
| Q1       | Output | HEATER_RELAY      | SSR control — heater circuit            | Relay NO    |
| Q2       | Output | SERVO_POWER       | 24V→5V BEC power enable for servos      | Relay NO    |
| Q3       | Output | DUT_POWER         | DUT power enable relay                  | Relay NO    |
| Q4       | Output | ALARM_BEACON      | External 24V alarm beacon               | Relay NO    |

---

## Modbus TCP Register Map

Enable Modbus server in LOGO! Soft Comfort: **Tools → Ethernet → Modbus server → Enable**.

| Modbus address | Access | LOGO! variable | Description                              |
|----------------|--------|----------------|------------------------------------------|
| Coil 8192      | R/W    | Q1 coil        | Heater relay software command            |
| Coil 8193      | R/W    | Q2 coil        | Servo power relay software command       |
| Coil 8194      | R/W    | Q3 coil        | DUT power relay software command         |
| Coil 8195      | R/W    | Q4 coil        | Alarm beacon software command            |
| Input 8192     | R      | I1 state       | E-stop physical state (1=safe)           |
| Input 8193     | R      | I2 state       | Door interlock (1=closed)                |
| Input 8194     | R      | I3 state       | HW overtemp (1=tripped)                  |
| Input 8195     | R      | I4 state       | Manual start button (1=pressed)          |
| Input 8196     | R      | I5 state       | Manual stop button (1=pressed)           |

---

## Function Block Program

### Network 1 — Safety gate (internal flag M1)

M1 is HIGH only when ALL physical safety conditions are met. M1 feeds into all output rungs.

```
FBD Network 1:
  [I1 NO contact] ──┬──────────────────────────────────┐
  [I2 NO contact] ──┤                                  ├──►(AND gate) ──►(M1)
  [I3 NC contact] ──┘   (I3 = NC because it's a NO HW switch
                          that makes on fault, so we use NC contact
                          to get "safe = switch open = contact closed logic")
```

**Note:** I1 and I2 are NC switches → when safe, they provide 24V → LOGO! reads HIGH → use NO contact in ladder. I3 is NO switch → when tripped, provides 24V → use NC contact so M1 goes LOW when I3 is HIGH (tripped).

### Network 2 — Run flag SR latch (M2)

```
SET input:   [I4 NO] OR [Modbus run coil (VM)]
RESET input: [I5 NC] OR [M1 NC] (safety gate fault)
Output:      ──►(M2 = "run permitted")
```

### Network 3 — Q1 Heater relay

```
[M1 NO] ──[M2 NO] ──[Modbus Q1 coil]──►(Q1)
```
Heater energises only when: safety OK AND run latched AND software commands it.

### Network 4 — Q2 Servo power relay

```
[M1 NO] ──[Modbus Q2 coil]──►(Q2)
```
Servo power requires only the safety gate (not the run latch — servos may be positioned during idle).

### Network 5 — Q3 DUT power relay

```
[M1 NO] ──[M2 NO] ──[Modbus Q3 coil]──►(Q3)
```
DUT power follows the same pattern as the heater.

### Network 6 — Q4 Alarm beacon

```
[I1 NC] ──OR──[I2 NC] ──OR──[I3 NO] ──►(Q4)
```
Alarm fires immediately on any safety fault, regardless of software state.
- I1 NC = alarm when E-stop tripped (I1=LOW)
- I2 NC = alarm when door open (I2=LOW)
- I3 NO = alarm when HW overtemp active (I3=HIGH)

---

## LOGO! Soft Comfort Setup Procedure

1. Install LOGO! Soft Comfort from the CD included with the PLC.
2. Open Soft Comfort → **File → New** → select **LOGO! 8 / 12/24RC**.
3. Set the PC's network adapter to the same subnet as the LOGO! (default: 192.168.0.x).
4. Connect USB programming cable (or Ethernet if your LOGO! model supports it).
5. **Tools → Ethernet settings → IP address**: set to `192.168.1.100` (or match `rig_config.json`).
6. **Tools → Ethernet → Modbus server**: enable, port 502.
7. Enter the FBD program as specified in Networks 1–6 above.
8. **File → Transfer to LOGO!** — wait for transfer confirmation.
9. Set LOGO! to **RUN mode** via the front-panel button or Soft Comfort.
10. Verify: press Manual Start (I4) → M2 should latch. Check with Soft Comfort online monitor.

---

## Verification Checklist

Before connecting the heater or DUT:

- [ ] LOGO! powered up, showing RUN on display
- [ ] Modbus TCP responding on port 502 (test with `logo_driver.py ping()`)
- [ ] I1 reads HIGH (E-stop button released, NC circuit closed)
- [ ] I2 reads HIGH (door closed)
- [ ] I3 reads LOW (HW thermostat under 120°C, NO contact open)
- [ ] Pressing Manual Start → M2 latches → Q1/Q3 coils can be enabled via software
- [ ] Pressing E-stop → I1 goes LOW → M1 goes LOW → Q1, Q3 de-energise immediately
- [ ] Q4 alarm fires when E-stop pressed
- [ ] Modbus write to Q1 coil with safety gate open → Q1 relay clicks
- [ ] Modbus write to Q1 coil with E-stop pressed → Q1 does NOT energise

---

<p align="center"><sub>© Juniper Design · <a href="https://juniperdesign.com">juniperdesign.com</a></sub></p>
