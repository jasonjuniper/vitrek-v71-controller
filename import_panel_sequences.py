"""
import_panel_sequences.py
-------------------------
Transcription of the three test sequences built on the V71 front panel,
read from photographs of the EDIT SEQUENCE screens on 2026-08-17.

The V7X protocol is write-only for sequences, so this file IS the backup —
there is no way to re-read these definitions from the instrument. Treat it as
the source of truth and keep it in version control.

PROVENANCE — read before editing any value here.

    The test values were set up on the instrument by an electrical engineer.
    They are transcribed here EXACTLY as found; nothing has been adjusted,
    rounded, or "corrected". Do not change a number in this file to make a
    test pass or to match an expectation. Any change to a test value is the
    responsible engineer's call, gets made on the instrument first, and is
    then re-transcribed here.

    External equipment is connected to the DUT and sits in the ground /
    continuity measurement path. Readings therefore reflect the fixture as a
    whole, not a bare conductor — which is why the CONT threshold looks loose
    if you evaluate it in isolation. See docs/test-sequence-review-questions.md.

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
        "description": ("Continuity only. Values set by the responsible electrical "
                        "engineer; transcribed verbatim from V71 front panel "
                        "sequence #1 on 2026-08-17. Panel ZERO offset was 0 ohm. "
                        "External equipment is in the measurement path."),
        "steps": [
            {"type": "CONT", "dwell": "5", "max_ohm": "5000"},
        ],
    },
    {
        "name": "HIGH POT",
        "description": ("AC withstand only, breakdown detection with no leakage "
                        "limits (panel LIMITS: Breakdown Only). Values set by "
                        "the responsible electrical engineer; transcribed "
                        "verbatim from V71 front panel sequence #2 on "
                        "2026-08-17. Arc limit 20 mA lives in CONFIG, not here."),
        "steps": [
            # min_leakage and max_leakage omitted = LIMITS: Breakdown Only
            {"type": "ACW", "voltage": "1500", "ramp": "1", "dwell": "1",
             "grounded": "0"},
        ],
    },
    {
        "name": "CONT HIGHPOT",
        "description": ("Continuity then AC withstand. Values set by the "
                        "responsible electrical engineer; transcribed verbatim "
                        "from V71 front panel sequence #3 on 2026-08-17. Steps "
                        "are identical to sequences #1 and #2 back to back; "
                        "ON FAIL is Stop Running Sequence, so a CONT failure "
                        "prevents the 1500 V step from running."),
        "steps": [
            {"type": "CONT", "dwell": "5", "max_ohm": "5000"},
            {"type": "ACW", "voltage": "1500", "ramp": "1", "dwell": "1",
             "grounded": "0"},
        ],
    },
]


# Marker in app_settings so the first-run seed happens exactly once per install.
DEFAULT_SEED_MARKER = "default_sequences_seeded"


def seed_default_sequences(force: bool = False, db_path=None) -> dict:
    """Idempotently ensure the three panel-transcribed sequences exist.

    Called on app startup so a freshly-installed / re-imaged station comes up
    with CONT TEST / HIGH POT / CONT HIGHPOT already in the dropdown instead of
    an empty list (the results DB lives in ProgramData and is wiped on re-image).

    Safe by design — it NEVER overwrites or resurrects a sequence an admin has
    since edited or deleted:
      * it only *creates* names that are entirely absent, and
      * it runs only once, guarded by an app_settings marker, so a default the
        admin later deletes on purpose does not come back on the next restart.

    force=True re-runs the create-if-missing pass regardless of the marker (still
    never touching sequences that already exist) — handy for tests/recovery.
    """
    kw = {} if db_path is None else {"db_path": db_path}
    if not force and db.get_setting(DEFAULT_SEED_MARKER, **kw) == "1":
        return {"seeded": False, "created": [], "reason": "already seeded"}
    existing = {s["name"] for s in db.list_sequences(instrument="hipot",
                                                     active_only=False, **kw)}
    created = []
    for seq in SEQUENCES:
        if seq["name"] in existing:
            continue
        db.create_sequence(seq["name"], seq["steps"],
                           description=seq["description"],
                           instrument="hipot",
                           created_by="default seed (panel transcription)", **kw)
        created.append(seq["name"])
    db.set_setting(DEFAULT_SEED_MARKER, "1", **kw)
    return {"seeded": True, "created": created}


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
