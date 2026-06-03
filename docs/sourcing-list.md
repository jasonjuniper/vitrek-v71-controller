<p align="center"><img src="../assets/juniper-banner.svg" alt="JUNIPER" width="900"></p>

# Sourcing List — Automated Test Station

> **📄 Print-ready PDF:** [`docs/pdf/sourcing-list.pdf`](pdf/sourcing-list.pdf)

Shopping list for the breadboard prototype and first full build. Grouped by priority — Priority 1 blocks breadboarding and must be ordered before any bench work can start.

Legend: ✅ on hand · ⏳ ordered · 🛒 to source

Approximate prices are USD, mid-2026 retail. Use as ballparks, not quotes.

---

## Priority 1 — Order Today (blocks breadboarding)

These parts are required before you can do anything on the breadboard. Order all of them in one cart.

| # | Item | Qty | Status | Approx $ | Notes |
|---|------|-----|--------|----------|-------|
| 1 | MAX31855 thermocouple amplifier breakout | 3 | 🛒 | ~$5 ea | Amazon search: **"MAX31855 thermocouple amplifier breakout"** — Adafruit #269 or compatible clone. Exposes VCC, GND, DO, CS, CLK on a 5-pin SIP header. Verify breakout has 3.3V regulator or is marked 3.3V-only — the MAX31855 is NOT 5V. |
| 2 | Solderless breadboard, 830-point, full-size | 1 | 🛒 | ~$8 | Only if not already on hand. Amazon: **"830 point solderless breadboard"**. A half-size (400-point) will be too tight for 3 modules + protection resistors — get full-size. |
| 3 | E-stop mushroom button, NC, latching, 22mm panel mount | 1 | 🛒 | ~$6 | Amazon: **"22mm latching mushroom emergency stop button NC"** — red head, normally-closed contacts. Must be latching (twist-to-release), not momentary. This is a safety-critical component — don't use the cheapest listing; pick one with 10A/250VAC contact rating. |
| 4 | Green momentary pushbutton, NC, 22mm panel mount | 1 | 🛒 | ~$3 | Amazon: **"22mm momentary pushbutton green NC normally closed"** — manual START button wired to LOGO! I4. NC type allows fail-safe wiring. |
| 5 | Red momentary pushbutton, NC, 22mm panel mount | 1 | 🛒 | ~$3 | Amazon: **"22mm momentary pushbutton red NC normally closed"** — manual STOP button wired to LOGO! I5. |

**Priority 1 subtotal:** ~$25–30

---

## Priority 2 — Order Soon (needed for heater circuit)

Order these alongside Priority 1 or immediately after. The heater circuit cannot be tested or brought up without them.

| # | Item | Qty | Status | Approx $ | Notes |
|---|------|-----|--------|----------|-------|
| 6 | Fotek SSR-40DA solid-state relay | 1 | 🛒 | ~$8–12 | Amazon: **"SSR-40DA solid state relay"**. Spec: 3–32V DC control input, 24–480V AC load side, 40A, zero-crossing. At 150W/120VAC the load is 1.25A — well within rating. Must be genuine Fotek or a quality clone; cheap counterfeits have caused fires. Check seller reviews carefully. |
| 7 | 150W 120VAC cartridge heater | 1 | 🛒 | ~$12–18 | Amazon: **"cartridge heater 150W 120V"** or search Omega part **"CIR-1012/120V"**. Common size: 5/16" (8mm) diameter × 3" (76mm) long. Verify 120VAC specifically — 240VAC units exist and look identical. |
| 8 | KSD301 bimetallic thermostat disc, 120°C, NC | 2 | 🛒 | ~$1–2 ea | Amazon: **"KSD301 thermostat 120C NC normally closed"** — snap-disc type, mounts directly against heater body. 120°C trip point = hardware overtemp cutout wired to LOGO! I3. Buy 2: one installed, one spare. |
| 9 | Thermal cutoff fuse, 150°C, 10A, axial | 5 | 🛒 | ~$1–2 ea | DigiKey/Mouser: search **"thermal cutoff fuse 150C 10A axial"**. Or Amazon: **"152C thermal fuse 10A"** — 152°C is a common standard rating close to 150°C. Spec: one-time non-resettable TCO type. Must be rated ≥10A. Clip directly to heater body — NOT in free air. Buy 5: 1 installed + 4 spares (a blown fuse means investigate; don't reuse). |
| 10 | Aluminium heatsink for SSR | 1 | 🛒 | ~$5–8 | Amazon: **"SSR heatsink aluminum solid state relay"** — look for a dedicated SSR heatsink with pre-drilled mounting holes matching 40A SSR footprint (57.5mm × 44mm typical). Without a heatsink the SSR overheats at sustained duty. A DIN-rail mounted extrusion with fins ≥50 cm² face area also works. |

**Priority 2 subtotal:** ~$35–50

---

## Priority 3 — Servo Vent Control

Order when you are ready to wire the vent servos. Not needed for initial heater bring-up.

| # | Item | Qty | Status | Approx $ | Notes |
|---|------|-----|--------|----------|-------|
| 11 | Tower Pro MG90S metal-gear servo (or SG90 plastic) | 2 | 🛒 | ~$3–5 ea | Amazon: **"MG90S metal gear servo"** — metal gear recommended for repeated vent duty. SG90 plastic gear is fine for light loads. Standard 50Hz PWM, 500–2500µs pulse, 5V supply. |
| 12 | 5V 2A BEC / DC-DC converter, 24V input | 1 | 🛒 | ~$5 | Amazon: **"24V to 5V DC DC converter 2A step down"** — powers servo rail from LOGO! Q2 24V relay output. Needs to handle 1A peak (2 servos at stall). Get a switching BEC, not a linear regulator (24→5V linear = 19V × 1A = 19W of heat = not good). |

**Priority 3 subtotal:** ~$12–20

---

## Already Have (verify before ordering)

Check your bench stock before adding these to a cart.

| # | Item | Qty | Status | Notes |
|---|------|-----|--------|-------|
| 13 | K-type thermocouples with leads | 3 | ✅ | One per MAX31855 channel — TC1 ambient, TC2 DUT, TC3 heater. Yellow wire = positive (ANSI standard). |
| 14 | Dupont jumper wires (M-F, F-F assortment) | — | ✅ | For breadboard-to-RPi header connections. |
| 15 | Siemens LOGO! 12/24RCE PLC | 1 | ✅ | Already installed / on hand. |
| 16 | 24V DIN rail PSU (Meanwell HDR-60-24 or equivalent) | 1 | ✅ | Powers LOGO! and relay coils. |
| 17 | WeMos D1 Mini / NodeMCU ESP8266 | 1 | ✅ | Used for status-display sketch. NodeMCU (27mm row-spacing) will work on a breadboard; D1 Mini preferred for clean mounting. |
| 18 | Arduino resistor kit — 100Ω and 10kΩ | — | ✅ | Probably — confirm you have at least 3× 10kΩ (CS pull-ups) and 3× 100Ω (protection resistors). If stock is low, order a 1/4W resistor assortment on Amazon: **"resistor assortment kit 1/4W 600 piece"**. |
| 19 | 100nF (0.1µF) ceramic capacitors | 3+ | ✅ | Probably — confirm at least 3× 100nF for MAX31855 VCC decoupling. |
| 20 | SSD1306 128×64 OLED display (I²C) | 1 | ✅ | Check Arduino kit. If not on hand: Amazon **"SSD1306 0.96 inch OLED I2C"** ~$4. |
| 21 | Raspberry Pi 4B | 1 | ❓ | **Required for GPIO.** The Flask app can run on the NUC for development, but reading MAX31855 via SPI and driving GPIO12/13/18 for SSR and servos requires RPi GPIO. If you don't have one, add a RPi 4B (2GB) to Priority 1 (~$35–45, Adafruit or PiShop — avoid grey-market pricing). The RPi 4B must have SPI enabled (`raspi-config → Interface Options → SPI → Enable`). |

---

## Note on Raspberry Pi

The thermal controller as designed requires an **RPi 4B** for GPIO access:
- SPI0 hardware bus (GPIO8/7/25 for CS, GPIO11/9 for CLK/MISO) → reads MAX31855 modules
- GPIO12 hardware PWM → heater SSR duty cycle control
- GPIO13, GPIO18 hardware PWM → servo A and servo B vent control

The **Flask HMI app** (`app.py`) can run on any host (NUC, laptop) during development with the hardware-dependent routes stubbed out. But for full rig operation with live temperature feedback and heater control, you need the RPi on the bench.

If you don't already have a Pi 4B, add it to the Priority 1 order. The 2GB RAM model is sufficient. Adafruit and PiShop.us are the most reliable US retailers; avoid Amazon third-party listings that may be overpriced or grey-market.

---

## Total Cost Estimate

| Priority bucket | Subtotal |
|-----------------|----------|
| Priority 1 — breadboard unblock | ~$25–30 |
| Priority 2 — heater circuit | ~$35–50 |
| Priority 3 — servos | ~$12–20 |
| RPi 4B (if needed) | ~$35–45 |
| **Total to source** | **~$110–145** |

Minimum spend to get a breadboard bench test running (Priority 1 only, assuming RPi on hand): **~$25–30**.

---

<p align="center"><sub>© Juniper Design · <a href="https://juniperdesign.com">juniperdesign.com</a></sub></p>
