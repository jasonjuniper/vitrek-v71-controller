<p align="center"><img src="assets/juniper-banner.svg" alt="JUNIPER · Lighting · Power Solutions · Systems" width="900"></p>

# Juniper Automated Test Station

A Python/Flask application driving the Juniper test bench: the **Vitrek V71
Hi-Pot Tester** over USB or RS-232, a **Siglent SDL1020X-E** DC electronic load,
and an always-on **Siemens LOGO! PLC** thermal rig. Results are stored in SQLite
and exported to a branded Excel workbook for SharePoint. Built at
[Juniper Design](https://juniperdesign.com).

> 📄 **Print-ready PDF:** [`docs/pdf/README.pdf`](docs/pdf/README.pdf)

---

## Features

- **Two access levels** — operators run approved sequences with no login; admins
  define them. See [`docs/access-control.md`](docs/access-control.md)
- **Saved test sequences** — reviewed, versioned definitions rather than
  parameters typed at the bench
- **Instrument verification (PVD)** — daily performance verification against a
  Vitrek APVD, with the actual measured values recorded, not just a pass light.
  See [`docs/pvd-verification.md`](docs/pvd-verification.md)
- **USB and RS-232** — USB via the Silicon Labs CP2110 HID-to-UART DLL; RS-232
  via pyserial
- **Full V7X command set** — ACW, DCW, IR, GB, CONT, PAUSE, HOLD steps
- **DC load + thermal rig** — SDL1020X-E load batteries, PEC-0063 thermal
  qualification, continuous 1 Hz sensor recording
- **Web UI** — nothing to install on operator machines, just open the page
- **Excel export** — colour-coded workbook covering test sessions, thermal
  qualification and instrument verification

---

## Quick Start

```bash
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`.

**Connect the V71:** set `CONFIG MENU → INTERFACE = USB` on the front panel and
plug in the USB-B cable, or `= RS232` with a fully-wired 9-wire null-modem cable
(hardware RTS/CTS handshaking is required).

### First run

1. Click **Set up admin** in the top bar and choose a password. It is stored only
   as a PBKDF2 hash — keep the password itself in 1Password.
2. As admin, open **HiPot → Sequence Builder** and create at least one sequence.
   Operators cannot run anything until one exists.
3. Baseline the PVD profile before relying on verification verdicts — see
   [`docs/pvd-verification.md`](docs/pvd-verification.md).

---

## Instruments

| Instrument | Interface | Notes |
|---|---|---|
| Vitrek V71 HiPot | USB HID-to-UART, or RS-232 | ACW, DCW and CONT modes only — no IR, no GB |
| Siglent SDL1020X-E DC Load | TCP/LAN, VISA or USB CDC | |
| Siemens LOGO! PLC + thermal rig | Modbus TCP | Always on, independent of instrument selection |

Instrument selection is mutually exclusive — one at a time — but the thermal rig
runs in parallel with any instrument test, and sensor data is continuously
recorded so results can be correlated against the timeline.

---

## Project Structure

```
├── app.py                Flask app: REST API + embedded UI for every page
├── auth.py               Roles, admin password hashing, @admin_required
├── v71_driver.py         V71 USB/serial driver (ctypes + pyserial)
├── sdl1020x_driver.py    Siglent DC load driver
├── pvd_test.py           Instrument verification runner
├── pvd_profiles.json     Verification profiles (APVD-74 / APVD-7X)
├── test_battery.py       Multi-step DC load test batteries
├── pec0063_test.py       PEC-0063 thermal qualification
├── database.py           SQLite schema and CRUD
├── excel_export.py       Branded Excel workbook generator
├── plc/                  LOGO! PLC driver, thermal controller, rig_config.json
├── hipot_results.db      SQLite database (auto-created; gitignored)
└── docs/
    ├── access-control.md
    ├── pvd-verification.md
    ├── wiring-guide.md
    ├── hmi-architecture.md
    ├── plc-ladder-logic.md
    ├── breadboard-prototype.md
    ├── sourcing-list.md
    └── V7x_Series_Operating_Manual.pdf
```

---

## REST API

Routes marked **admin** require an admin session; everything else is available
to operators. Full detail in the two docs above.

### Auth and setup

| Method | Endpoint | | Description |
|---|---|---|---|
| GET  | `/api/auth/status` | | Current role |
| POST | `/api/auth/login` | | Elevate to admin |
| POST | `/api/auth/logout` | | Drop to operator |
| POST | `/api/auth/set_password` | | Set or change the admin password |
| GET/POST | `/api/settings/connection` | admin to write | Saved connection settings |

### Test sequences

| Method | Endpoint | | Description |
|---|---|---|---|
| GET | `/api/sequences` | | List saved sequences |
| POST | `/api/sequences` | admin | Create |
| PUT | `/api/sequences/<id>` | admin | Update (bumps revision) |
| DELETE | `/api/sequences/<id>` | admin | Retire (`?hard=1` deletes) |

### HiPot

| Method | Endpoint | | Description |
|---|---|---|---|
| POST | `/api/connect` | | Connect (operators use the saved settings) |
| POST | `/api/disconnect` | | Disconnect |
| POST | `/api/hipot/run_sequence` | | Run a saved sequence |
| POST | `/api/hipot/run` | admin | Run ad-hoc steps |
| POST | `/api/hipot/abort` · `/api/hipot/cont` | | Abort / continue |
| GET | `/api/hipot/status` · `/api/hipot/live` | | Run state and live V/A/Ω |

### Verification, results and export

| Method | Endpoint | | Description |
|---|---|---|---|
| POST | `/api/pvd/start` · `/api/pvd/ack` · `/api/pvd/stop` | admin | Run a verification |
| POST | `/api/pvd/baseline/promote` | admin | Adopt baseline measurements as nominals |
| GET | `/api/pvd/results` · `/api/pvd/verification_status` | | Verification history and currency |
| GET | `/api/sessions` · `/api/session/<id>` | | Test history |
| GET | `/api/export` · `/api/export/<id>` | | Excel export |

---

## USB Communication Details

The V71 uses a **Silicon Labs CP2110 HID-to-UART bridge**:

- `SLABHIDtoUART.dll` + `SLABHIDDevice.dll` (x64, from `software/drivers/`)
- USB VID `4292` (0x10C4), PID `34869` (0x8835)
- 115200 baud, 8N1, RTS/CTS flow control
- ASCII commands terminated with `\r\n`

**Key commands** (Section 6 of the operating manual):

| Command | Description |
|---|---|
| `*IDN?` / `*RST` / `*ERR?` | Identify / reset / read error register |
| `NOSEQ` | Clear and activate sequence #0 |
| `ADD,ACW,…` `ADD,DCW,…` `ADD,IR,…` `ADD,GB,…` `ADD,CONT,…` | Add a test step |
| `ADD,PAUSE,…` `ADD,HOLD,…` | Timed pause / operator prompt |
| `RUN` · `ABORT` · `CONT` · `RUN?` | Execution control |
| `RSLT?` · `STAT?` · `STEPRSLT?,<n>` | Overall, per-step and detailed results |
| `MEASRSLT?,<AMPS\|VOLTS\|OHMS>` | Live measurement |

There is **no remote command for AUTO PVD, SELF TEST or CAL VERIFY** — those are
front-panel only, which is why verification is implemented as a programmed
sequence. See [`docs/pvd-verification.md`](docs/pvd-verification.md).

---

## Excel Export

One workbook, several sheets:

- **Summary** — all test sessions, sortable and filterable
- **Per-session** — full step results, colour-coded
- **PEC-0063 Thermal Results** — thermal qualification against the UL limits
- **PVD Verification** — instrument verification, one row per measured point

Suitable for direct upload or auto-sync to SharePoint.

---

## Packaging & deployment

The bench machine (**HI-POT-TEST**) does not run this from source — it runs a
packaged single-file executable, deployed and kept current by the Juniper
inventory server.

- **Build the exe:** `powershell -ExecutionPolicy Bypass -File build-exe.ps1 -Version <x.y.z>`.
  This produces `dist/HiPotController-<x.y.z>.exe` (PyInstaller **onefile**, windowed)
  and prints its SHA256 + size. The build bundles `static/`, `pvd_profiles.json`,
  `plc/rig_config.json`, and the x64 Silicon Labs CP2110 DLLs at the path
  `v71_driver.py` loads them from. `build/` and `dist/` are gitignored — the exe is
  a build artefact, published to the server, not committed.
- **Persistent data (important for packaging).** Under a onefile exe,
  `os.path.dirname(__file__)` is the temporary extraction dir that Windows deletes
  on exit, so the app writes its **results DB** and the **mutable `pvd_profiles.json`**
  (baseline promotion rewrites it) to `C:\ProgramData\Juniper\HiPotController`
  instead — see `paths.py` (`data_dir()` / `bundled_dir()`). Read-only bundled
  resources (`rig_config.json`, brand assets) are read from the bundle. Running from
  source is unchanged: `data_dir()` resolves to the repo, so dev keeps its files in
  place.
- **Deploying to the fleet:** the exe is uploaded to the inventory server and the
  catalog package **"Juniper HiPot Controller"** (device-scoped to HI-POT-TEST) is
  bumped to the new version, with its install script pinned to the new exe's hash.
  The agent installs to `C:\Program Files\Juniper\HiPot Controller` on next check-in.

## Notes

- The DLLs must be present at `software/drivers/USB_DLLs_and_Headers/USB DLLs and Headers/x64/`, and the app must run as **x64** to match them
- After a DUT breakdown on USB the V71 may disconnect and reconnect; the driver
  surfaces the error — just reconnect from the UI
- RS-232 requires hardware handshaking; a 3-wire cable will not work
- `hipot_results.db` is gitignored: it holds the admin password verifier and the
  session signing key, neither of which belongs in the repository
