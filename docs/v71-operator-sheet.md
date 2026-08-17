# V71 Hipot Tester — Operator Sheet

**Station:** Juniper Automated Test Station · Vitrek V71
**Issue 1 · 17 August 2026**

---

## ⚠ Before you start

This tester applies **1500 volts** to the unit under test.

- Do not touch the DUT, the fixture, or the test leads while a test is running.
- The **INTERLOCK input is disabled** on this station. Nothing will automatically
  stop the test if a guard or door is opened. Treat the fixture as live from the
  moment you press START until the screen shows the test has finished.
- If anything looks or sounds wrong, press **STOP**.
- Report any failure to your supervisor. Do not re-run a failed unit to see if it
  passes the second time.

---

## Choosing a test

Pick the sequence that matches the job. If you are not sure which one, ask —
do not guess.

| Choose | When | What it does |
|--------|------|--------------|
| **CONT TEST** | Continuity check only | Checks a connection is present and good. No high voltage. |
| **HIGH POT** | Dielectric check only | Applies 1500 V for 1 second. |
| **CONT HIGHPOT** | Full test — the usual choice | Continuity first, then 1500 V. Stops if continuity fails. |

---

## Running a test

1. Fit the unit in the fixture and connect the test leads.
2. Press the button for the test you need.
3. Scan or type the **Order Number**.
4. Stand clear of the fixture.
5. Press **GO**.
6. Wait for the result. Do not touch anything until **PASS** or **FAIL** fills
   the screen.

If GO is greyed out, the grey text underneath says why — usually no test chosen,
no order number, or the tester is not connected.

Your name is set once at the start of a shift, not for every unit. If the name
in the top right is not yours, press **change**.

The full test takes about **8 seconds**: 5 seconds of continuity, then about
2 seconds at high voltage.

---

## Reading the result

**PASS** — every step stayed inside its limits. The unit is good.

**FAIL** — set the unit aside, tagged, and tell your supervisor. The screen shows
which step failed and why. Press **OK** to clear it and test the next unit. The
most common messages:

| Message | Meaning in plain terms |
|---------|------------------------|
| ABOVE MAX LIMIT | On the continuity step: the connection is too resistive, or a lead is loose. |
| BREAKDOWN DETECTED | The insulation broke down under 1500 V. |
| ARCING DETECTED | Arcing was detected during the high-voltage step. |
| ABORTED BY USER | Somebody pressed STOP. |
| INTERLOCK OPENED | A guard opened during the test. |

A failure is information, not a nuisance. It is the tester doing its job.

---

## If the tester will not connect

The station software talks to the V71 over the USB cable.

- Check the USB cable is plugged into both the tester and the PC.
- On the tester, the **CONFIG MENU** screen should show `IFACE: USB`. If it shows
  *Not Attached*, the cable is not seen — reseat it.
- If it still will not connect, stop and get help. Do not change any other
  setting on the tester.

---

## Do not change the test settings

The voltages, times and limits were set by an electrical engineer, and the units
are tested against them. Changing a setting — even to make a stubborn unit pass —
invalidates the test and every result recorded against it.

**If you think a setting is wrong, stop and escalate.** Never adjust it yourself.

The settings themselves cannot be recovered from the tester if they are lost,
which is another reason not to alter them. They are recorded in *V71 Test
Sequence Settings — Engineering Record*.

---

## Who to call

| Situation | Who |
|-----------|-----|
| A unit fails | Your supervisor |
| Tester will not connect, or shows an error | Station owner / engineering |
| You think a setting is wrong | Engineering — do not change it |
| Someone received a shock, or you suspect they might have | **Stop everything. Get help immediately.** |
