# Open questions on the V71 test sequences (UL context)

**Status:** raised 2026-08-17, answered at the ownership level the same day.
**Raised by:** Claude (Cowork session), during transcription of the front-panel
sequences into the controller webapp.

## Disposition (2026-08-17)

Jay's response, recorded here so this document is not mistaken for an open
action list:

- **The test values were set up by an electrical engineer**, not by Jay and not
  by me. They are not to be changed by either of us.
- **External equipment is connected to the DUT**, which affects the ground /
  continuity path. That is the most likely explanation for the 5 kΩ CONT
  threshold reading oddly in isolation — it is not measuring a bare bonding
  conductor, and Section 1 below was written without knowledge of the external
  equipment.

Sections 1–4 are therefore **closed as owned by the responsible engineer**. The
sequences have been transcribed to software exactly as found on the instrument,
with no values altered. Sections 5–8 concern process and equipment state rather
than test values and remain worth a look, but none of them block use.

The value of what remains is as a record of what the settings *are* and why
someone unfamiliar might misread them — not as a challenge to them.

## Read this first

I am not an electrical engineer and not a certification authority. I have not
read UL 1310 or UL 962A — both are paywalled, and I could not verify a single
numeric requirement in either against its actual text. Everything below comes
from reading the V7X operating manual, the sequence settings as photographed
from the instrument on 2026-08-17, and general industry material about hipot
and grounding tests.

Nothing here should be treated as a compliance judgement. Each item is a
question for whoever owns the compliance decision — a qualified engineer, the
NRTL/UL representative for the listing, or whoever wrote the test spec these
sequences are meant to implement. Several may already have good answers that
simply are not written down anywhere I can see.

The reason I am writing it down at all: the sequences are now reproducible from
software and will get easier to run and to copy. Assumptions baked in at this
stage propagate.

## What the instrument is currently set to

Transcribed from the EDIT SEQUENCE screens (see `import_panel_sequences.py`):

| Seq | Name | Step | Settings |
|-----|------|------|----------|
| 1 | CONT TEST | 1 | CONT, dwell 5 s, max 5 kΩ, ZERO 0 Ω |
| 2 | HIGH POT | 1 | ACW, 1500 V, ramp 1 s, dwell 1 s, DUT Isolated, LIMITS Breakdown Only |
| 3 | CONT HIGHPOT | 1 | CONT, dwell 5 s, max 5 kΩ |
| 3 | CONT HIGHPOT | 2 | ACW, 1500 V, ramp 1 s, dwell 1 s, Isolated, Breakdown Only |

Global config (CONFIG MENU): IFACE USB (Not Attached), VICL 0, DIO Inputs Off,
START Requires Stop before Start, BEEP On, LOCK Disabled, FREQ 60 Hz,
**ARC 20 mA**, RAMP DOWN Fast Discharge, ON FAIL Stop Running Sequence.

---

## 1. The CONT step, and whether it is standing in for a grounding test

> **Closed.** Set by the responsible electrical engineer, and external equipment
> is in the measurement path — the step is not measuring a bare bonding
> conductor, which is the assumption the concern below was built on. Retained
> for context only.

The V71 CONT step is a low-current continuity measurement. It answers "is there
a conductive path here." The pass window is currently ≤ 5 kΩ.

If that step is only meant to confirm a wire is present and landed, 5 kΩ is a
defensible threshold and there is no issue.

If it is standing in for verification of a **protective earth / bonding
connection**, then the threshold is in the wrong range by orders of magnitude —
bonding requirements are typically expressed in the tens or low hundreds of
milliohms, verified at substantial current (commonly cited figures are 10 A to
40 A, with 25 A a frequent value). A 5 kΩ pass window would accept a corroded
joint, a loose fastener, a single surviving wire strand, or a connection that
would fail under fault current.

The equipment constraint matters here and is not fixable by changing a setting:
**the V71 has no ground bond (GB) capability.** Within the V7X family, GB is
available on the V74 and V77. The V71 supports ACW, DCW and CONT only. So if
the governing standard calls for a grounding impedance test at current, no
configuration of this instrument satisfies it — that is an equipment gap, not a
settings gap, and it should be resolved before the sequences get relied on.

Vitrek's own guidance is that continuity tester requirements should be built
backward from the test method the governing product standard calls for, and
specifically cautions that a low-current continuity check may miss weak joints
and poor terminations that a higher-current test is designed to reveal.

**Questions:** Which document specifies this step? Is it a presence check or a
bonding verification? If bonding — what current and resistance limit does the
governing standard require, and is a V71 the right instrument for it?

## 2. ACW at 1500 V for 1 second

> **Closed.** Set by the responsible electrical engineer; values not altered.

1500 V is a common dielectric test voltage. A 1-second dwell is also common —
but usually as the *production-line* variant of a longer type test, and the
usual convention is that shortening the dwell comes with **raising** the
voltage (a frequently cited rule is production testing at 120 % of the
one-minute type-test voltage for one second).

So 1500 V for 1 s could be exactly right, or it could be the one-minute type
voltage applied for the production duration without the compensating increase.
I cannot tell which from the instrument.

**Questions:** What is the type-test voltage and duration for this product, and
what does the governing standard permit as the production-line equivalent? Does
1500 V / 1 s match that, or should it be a higher voltage for the short dwell?

Related and smaller: the V7X applies RAMP before DWELL, and dwell does not
include ramp time. Total energised time per step is therefore ~2 s. Worth
confirming the standard's duration is measured the way the instrument measures
it.

## 3. "Breakdown Only" means no leakage limit at all

> **Closed.** Set by the responsible electrical engineer; values not altered.

Both ACW steps have MIN and MAX leakage set to NONE. Per the V7X manual, the
instrument still detects breakdown in that state, so the step is not inert.
What it does *not* do is apply any numeric window to leakage current.

A DUT drawing high but non-breakdown leakage passes. If the applicable standard
specifies a maximum leakage current for the dielectric test, this configuration
does not enforce it.

**Question:** Does the governing standard specify a leakage current limit for
this test, and if so should MAX LIMIT be set rather than left at NONE?

## 4. ARC 20 mA is armed, and it lives outside the sequence

> **Closed.** Set by the responsible electrical engineer; values not altered.

Arc detection is enabled at 20 mA, so arcing above that fails a step. That is a
real pass/fail criterion applied to every ACW step — but it is stored in the
CONFIG MENU, **not in the sequence**.

Consequence: the same named sequence produces different pass/fail behaviour on
two instruments configured differently, or on the same instrument after a
`RESET CONFIG`. Nothing in the sequence definition records that arc detection
was part of the acceptance criteria.

This is the concrete reason to capture a config backup from the instrument and
keep it alongside the sequence definitions in version control. The webapp can
now do this (HiPot page → Instrument Config → Back Up Settings).

**Questions:** Is 20 mA a specified value or a default someone accepted? Should
it be recorded as part of the test method?

## 5. Calibration and verification status

As of this writing the V71 is in calibration and the PVD verification profile
has not been baselined, so the station records verification values but cannot
issue a PASS. Production safety testing is normally expected to run on
calibrated equipment with traceable records.

**Question:** What is the calibration/verification status required before these
sequences are used for anything that gets recorded against a listing, and does
the current state meet it?

## 6. Interlock disabled (operator safety, not test validity)

`DIO: Inputs Off` disables the interlock input. At 1500 V on an open bench this
is worth a deliberate decision rather than inheriting a default. It does not
affect whether the test is valid — it affects whether someone can contact a
live fixture.

**Question:** Is there a physical interlock on the fixture, and should DIO be
enabled to honour it?

## 7. Sequences are not traceable to a specification

The sequences are named CONT TEST, HIGH POT and CONT HIGHPOT. Nothing in them
records which product, part number, or spec paragraph they implement, or who
approved the values. The webapp has a description field per sequence that is
currently carrying my transcription note rather than that information.

**Question:** Which document authorises these values, and should its identifier
live in the sequence description so results are traceable back to it?

## 8. Two sources of truth from here on

The webapp can now push sequences to the instrument, and the instrument still
has its own panel-stored copies. Nothing keeps them in sync, and the protocol
cannot read the panel copies back to compare.

**Suggestion:** decide which one is authoritative. If it is the webapp, the
panel sequences should eventually be treated as disposable; if it is the panel,
`import_panel_sequences.py` needs re-verifying by eye whenever the panel
changes. Silent divergence between the two is the failure mode.

---

## Sources

General background only — none of these are the governing standard, and none
were used to derive a numeric requirement:

- [Ground Continuity Tester Requirements — Vitrek](https://vitrek.com/ground-continuity-tester-requirements/)
- [Ground Continuity, Polarization, and Ground Bond Tests — Chroma](https://www.chromausa.com/applications/ground-bond-tests/)
- [The 25-Amp Grounding Impedance Test — In Compliance Magazine](https://incompliancemag.com/article/the-25-amp-grounding-impedance-test/)
- [Dielectric withstand test — Wikipedia](https://en.wikipedia.org/wiki/Dielectric_withstand_test)
- [Tutorial on Safety Standard Compliance for Hipot Testing — Chroma (PDF)](http://www.chromausa.com/pdf/app-notes/AN%20-%2019071,%2019073%20-%20Tutorial%20on%20Safety%20Standard%20Compliance%20for%20Hipot%20Testing%20-%2004052013.pdf)
- V7X Series Operating Manual, August 8 2025 — `docs/V7x_Series_Operating_Manual.pdf`
