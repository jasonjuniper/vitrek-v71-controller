"""
import_panel_sequences.py
-------------------------
Transcription of the three test sequences built on the V71 front panel,
read from photographs of the EDIT SEQUENCE screens on 2026-08-17.

The V7X protocol is write-only for sequences, so this file IS the backup —
there is no way to re-read these definitions from the instrument. Treat it as
the source of truth and keep it in version control.

Source screens, field by field:

  Sequence #1  "CONT TEST"    (1 step)
    Step 1  TYPE: CONT | DWELL: 5sec | LIMITS: 5kOhm max | ZERO: 0 Ohm

  Sequence #2  "HIGH POT"     (1 step)
    Step 1  TYPE: ACW | LEVEL: 1500V | DUT: Isolated
            RAMP: 1sec | DWELL: 1sec | LIMITS: Breakdown Only

  Sequence #3  "CONT HIGHPOT" (2 steps)
    Step 1  CONT, 5sec, 5kOhm max          (identical to Sequence #1)
    Step 2  ACW, 1500V, 1sec, Breakdown Only (identical to Sequence #2)

Notes carried over from the panel:
  * ZERO was 0 Ohm on the CONT step, so nothing is lost to the interface's
    lack of a per-step ZERO field. Had it been non-zero, these steps could not
    have been reproduced faithfully.
  * "Breakdown Only" means BOTH leakage limits are NONE. The V7X still detects
    breakdown; it just applies no numeric window to the leakage reading. This
    is represented by omitting min_leakage and max_leakage entirely — do not
    fill in a default.

Run:  python import_panel_sequences.py [--replace]
"""

import sys
import database as db

SEQUENCES = [
    {
        "name": "CONT TEST",
        "description": ("Continuity only. Transcribed from V71 front panel "
                        "sequence #1 on 2026-08-17. Panel ZERO offset was 0 ohm."),
        "steps": [
            {"type": "CONT", "dwell": "5", "max_ohm": "5000"},
        ],
    },
    {
        "name": "HIGH POT",
        "description": ("AC withstand only, breakdown detection with no leakage "
                        "limits. Transcribed from V71 front panel sequence #2 "
                        "on 2026-08-17."),
        "steps": [
            # min_leakage and max_leakage omitted = LIMITS: Breakdown Only
            {"type": "ACW", "voltage": "1500", "ramp": "1", "dwell": "1",
             "grounded": "0"},
        ],
    },
    {
        "name": "CONT HIGHPOT",
        "description": ("Continuity then AC withstand. Transcribed from V71 "
                        "front panel sequence #3 on 2026-08-17. Steps are "
                        "identical to sequences #1 and #2 run back to back."),
        "steps": [
            {"type": "CONT", "dwell": "5", "max_ohm": "5000"},
            {"type": "ACW", "voltage": "1500", "ramp": "1", "dwell": "1",
             "grounded": "0"},
        ],
    },
]


def main() -> int:
    replace = "--replace" in sys.argv
    existing = {s["name"]: s for s in db.list_sequences(instrument="hipot",
                                                        active_only=False)}
    for seq in SEQUENCES:
        prior = existing.get(seq["name"])
        if prior and not replace:
            print(f"skip    {seq['name']} — already present as id {prior['id']} "
                  f"(pass --replace to overwrite)")
            continue
        if prior:
            db.update_sequence(prior["id"], name=seq["name"],
                               steps=seq["steps"],
                               description=seq["description"])
            print(f"updated {seq['name']} (id {prior['id']})")
        else:
            new_id = db.create_sequence(seq["name"], seq["steps"],
                                        description=seq["description"],
                                        instrument="hipot",
                                        created_by="panel transcription")
            print(f"created {seq['name']} (id {new_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
