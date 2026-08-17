# V71 Test Sequence Settings — Engineering Record

**Instrument:** Vitrek V71 AC/DC Hipot Safety Tester
**Station:** Juniper Automated Test Station (`hi-pot_UART` controller)
**Recorded:** 17 August 2026
**Revision:** 1
**Source:** Transcribed from the V71 front panel (EDIT SEQUENCE and CONFIG MENU screens)

---

## Purpose and status of this document

This is the written record of what the V71 is configured to do. The instrument's
communication protocol is **write-only for test sequences** — there is no command
that reads a stored sequence back out, and the unit has no file export. If the
V71 is reset, replaced, or its memory is corrupted, this document and the
matching `import_panel_sequences.py` in the controller repository are the only
way the sequences come back.

**The test values were set by the responsible electrical engineer.** They are
recorded here exactly as found. Nothing in this document authorises a change to
them. Any change is the engineer's decision, is made on the instrument, and is
then re-recorded here at a new revision.

External equipment is connected to the DUT and sits in the ground / continuity
measurement path. Readings therefore reflect the fixture as a whole rather than a
bare conductor.

---

## Summary

| # | Sequence name | Steps | Purpose |
|---|---------------|-------|---------|
| 1 | CONT TEST | 1 | Continuity check only |
| 2 | HIGH POT | 1 | AC dielectric withstand only |
| 3 | CONT HIGHPOT | 2 | Continuity, then dielectric withstand |

Sequence 3 is sequences 1 and 2 run back to back. Because **ON FAIL** is set to
*Stop Running Sequence*, a continuity failure in step 1 prevents the 1500 V step
from running.

---

## Sequence 1 — CONT TEST

One step. Continuity measurement, no high voltage applied.

| Panel field | Setting |
|-------------|---------|
| TYPE | CONT |
| DWELL | 5 sec |
| LIMITS | 5 kΩ max (no minimum) |
| ZERO | 0 Ω |

**Controller command:** `ADD,CONT,5.0,,5000.0`

A reading at or below 5 kΩ passes. The empty field between the two commas is the
minimum limit, left as NONE.

---

## Sequence 2 — HIGH POT

One step. AC dielectric withstand at 1500 V.

| Panel field | Setting |
|-------------|---------|
| TYPE | ACW |
| LEVEL | 1500 V |
| DUT | Isolated |
| RAMP | 1 sec |
| DWELL | 1 sec |
| LIMITS | Breakdown Only |

**Controller command:** `ADD,ACW,1500.0,1.0,1.0,,`

**"Breakdown Only" means both leakage limits are set to NONE.** Per the V7X
manual the instrument still detects breakdown in this state, in accordance with
most standards; what it does not do is apply a numeric pass/fail window to the
measured leakage current. The two trailing empty fields in the command are those
two limits.

RAMP runs before DWELL and is not included in it, so total energised time for
this step is approximately 2 seconds.

---

## Sequence 3 — CONT HIGHPOT

Two steps, run in order. Settings are identical to sequences 1 and 2.

| Step | Panel field | Setting |
|------|-------------|---------|
| 1 | TYPE | CONT |
| 1 | DWELL | 5 sec |
| 1 | LIMITS | 5 kΩ max |
| 1 | ZERO | 0 Ω |
| 2 | TYPE | ACW |
| 2 | LEVEL | 1500 V |
| 2 | DUT | Isolated |
| 2 | RAMP | 1 sec |
| 2 | DWELL | 1 sec |
| 2 | LIMITS | Breakdown Only |

**Controller commands:**

```
ADD,CONT,5.0,,5000.0
ADD,ACW,1500.0,1.0,1.0,,
```

---

## Global configuration (CONFIG MENU)

These settings apply to **every** sequence and are stored separately from them.
They are part of the acceptance criteria even though they do not appear in any
sequence definition. The same named sequence run on a differently-configured
unit can produce a different pass/fail result.

| Setting | Value | Effect |
|---------|-------|--------|
| IFACE | USB | Selects the USB (HID-to-UART) port for computer control |
| VICL | 0 Switch Units | No 964 switch matrix attached |
| DIO | Inputs Off | Digital I/O inputs, including INTERLOCK, are disabled |
| START | Requires Stop before Start | Front panel START must be preceded by STOP |
| BEEP | On | Audible indication enabled |
| LOCK | Disabled | No password required to change sequences or configuration |
| FREQ | 60 Hz | Test frequency for ACW steps |
| **ARC** | **20 mA** | **Arcing above 20 mA fails the step** |
| RAMP DOWN | Fast Discharge | Voltage is discharged quickly rather than ramped down |
| ON FAIL | Stop Running Sequence | A failed step halts the remaining steps |

**ARC deserves particular attention.** It is an active pass/fail criterion
applied to every ACW step, but it lives in the CONFIG MENU rather than in the
sequence. A `RESET CONFIG` on the instrument would set it to 0 (disabled) and
the HIGH POT sequence would quietly stop failing on arcing, with no change to
the sequence itself and nothing on screen to indicate the difference.

The controller webapp can capture these settings to a timestamped JSON file
(HiPot page → Instrument Config → **Back Up Settings**). Doing so after any
configuration change, and keeping the file with the project, is the practical
protection against this.

---

## What a backup does not cover

The following cannot be read from the instrument over the interface, by the
controller or by any other software, and exist only where they are written down:

- **The test sequences themselves.** Write-only protocol, as described above.
- **IFACE.** No remote command — deliberate, since changing it remotely could
  sever the connection issuing the command.
- **CONT ZERO and GB ZERO lead-resistance offsets** (UTILITY MENU). No remote
  command; these must be re-measured by hand after a reset.
- **Per-step ZERO offsets** inside CONT steps. Not settable over the interface at
  all. The CONT step above uses 0 Ω, so nothing is currently lost to this — but a
  future sequence that used a non-zero offset could not be fully reproduced by
  the controller.
- **The CONFIG MENU lock password.**

---

## Reproducing these sequences

On the controller station:

```
python import_panel_sequences.py
```

This writes all three sequences into the station database, from which they appear
in the operator dropdown on the HiPot page. The commands generated are verified
against the V7X manual's configuration field tables by `test_add_commands.py`.

To load a sequence into the instrument itself, run it once from the controller —
the controller programs sequence #0 over the interface at run time. Sequence #0
is volatile and is lost at power-off; the numbered stores 1–3 on the front panel
are non-volatile and hold the copies recorded in this document.

---

## Revision history

| Rev | Date | Change | By |
|-----|------|--------|----|
| 1 | 2026-08-17 | Initial record, transcribed from the instrument front panel | — |

---

## Verification

Recorded settings checked against the instrument front panel:

**Name:** ............................................  **Date:** ....................

**Signature:** .......................................

Settings authorised by (electrical engineer):

**Name:** ............................................  **Date:** ....................

**Signature:** .......................................
