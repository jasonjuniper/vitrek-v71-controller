"""
test_add_commands.py
--------------------
Verifies the ADD,... command strings the driver builds against the field
tables in the V7X Series Operating Manual, Section 6.

The V7X parses ADD steps purely by comma position, and a misplaced field is
not always rejected — it can be silently accepted as a different setting. That
makes positional correctness worth asserting explicitly rather than trusting.

Run:  python test_add_commands.py
"""

import sys
from v71_driver import V71Driver


class _Capture(V71Driver):
    """Records the ADD strings instead of sending them to hardware."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def add_step(self, step_cmd: str) -> None:   # bypass send + *ERR? check
        self.sent.append(step_cmd)

    def last(self) -> str:
        return self.sent[-1]


# (label, builder call, expected command)
#
# Expected strings are taken from, or derived directly from, the manual's
# CONFIGURATION FIELDS tables and their worked examples.
CASES = [
    # --- ACW: 1 ACW | 2 V | 3 ramp | 4 dwell | 5 min | 6 max | 7 GND ---
    ("ACW manual example (isolated, no min limit)",
     lambda d: d.add_acw_step(1000.0, 1.5, 60.0, 0.005),
     "ADD,ACW,1000.0,1.5,60.0,,0.005"),
    ("ACW grounded — GND must be field 7",
     lambda d: d.add_acw_step(1000.0, 1.5, 60.0, 0.005, grounded=True),
     "ADD,ACW,1000.0,1.5,60.0,,0.005,GND"),
    ("ACW with min limit in field 5",
     lambda d: d.add_acw_step(1500.0, 2.0, 30.0, 0.005, min_leakage_a=0.0001),
     "ADD,ACW,1500.0,2.0,30.0,0.0001,0.005"),
    ("ACW 'Breakdown Only' — both limits NONE",
     lambda d: d.add_acw_step(1500.0, 1.0, 1.0, max_leakage_a=None),
     "ADD,ACW,1500.0,1.0,1.0,,"),
    ("DCW 'Breakdown Only' — both limits NONE",
     lambda d: d.add_dcw_step(1000.0, 1.5, 60.0, max_leakage_a=None),
     "ADD,DCW,1000.0,1.5,60.0,,"),
    ("CONT max limit only (Sequence 1 as built on the panel)",
     lambda d: d.add_cont_step(5.0, max_ohm=5000.0),
     "ADD,CONT,5.0,,5000.0"),

    # --- DCW: ... | 7 GND | 8 CAP (both positional) ---
    ("DCW manual example (isolated, resistive)",
     lambda d: d.add_dcw_step(1000.0, 1.5, 60.0, 25e-6),
     "ADD,DCW,1000.0,1.5,60.0,,2.5e-05"),
    ("DCW grounded — GND in field 7, empty field 8",
     lambda d: d.add_dcw_step(1000.0, 1.5, 60.0, 25e-6, grounded=True),
     "ADD,DCW,1000.0,1.5,60.0,,2.5e-05,GND,"),
    ("DCW capacitive — empty field 7, CAP in field 8",
     lambda d: d.add_dcw_step(1000.0, 1.5, 60.0, 25e-6, capacitive=True),
     "ADD,DCW,1000.0,1.5,60.0,,2.5e-05,,CAP"),
    ("DCW grounded AND capacitive",
     lambda d: d.add_dcw_step(1000.0, 1.5, 60.0, 25e-6,
                              grounded=True, capacitive=True),
     "ADD,DCW,1000.0,1.5,60.0,,2.5e-05,GND,CAP"),

    # --- IR: 1 IR | 2 V | 3 dwell | 4 delay | 5 min | 6 max | 7 GND | 8 CAP ---
    ("IR manual example (no max limit, isolated, resistive)",
     lambda d: d.add_ir_step(1000.0, 60.0, 100e6),
     "ADD,IR,1000.0,60.0,0.0,100000000.0,"),
    ("IR capacitive — empty field 7, CAP in field 8",
     lambda d: d.add_ir_step(1000.0, 60.0, 100e6, capacitive=True),
     "ADD,IR,1000.0,60.0,0.0,100000000.0,,,CAP"),
    ("IR grounded with max limit",
     lambda d: d.add_ir_step(500.0, 30.0, 10e6, max_resistance_ohm=1e9,
                             grounded=True),
     "ADD,IR,500.0,30.0,0.0,10000000.0,1000000000.0,GND,"),

    # --- GB: 1 GB | 2 A | 3 dwell | 4 min | 5 max ---
    ("GB manual example (no min limit)",
     lambda d: d.add_gb_step(25.0, 5.0, 0.1),
     "ADD,GB,25.0,5.0,,0.1"),
    ("GB with min limit in field 4",
     lambda d: d.add_gb_step(25.0, 5.0, 0.1, min_ohm=0.01),
     "ADD,GB,25.0,5.0,0.01,0.1"),

    # --- CONT: 1 CONT | 2 time | 3 min | 4 max ---
    ("CONT manual example",
     lambda d: d.add_cont_step(5.0, 1.25, 1.75),
     "ADD,CONT,5.0,1.25,1.75"),
    ("CONT with no limits at all",
     lambda d: d.add_cont_step(5.0),
     "ADD,CONT,5.0,,"),

    # --- PAUSE / HOLD ---
    ("PAUSE manual example",
     lambda d: d.add_pause_step(5.0),
     "ADD,PAUSE,5.0"),
    ("HOLD manual example",
     lambda d: d.add_hold_step(60.0, "LINE 1", "LINE 2"),
     "ADD,HOLD,60.0,LINE 1,LINE 2"),
    ("HOLD sanitises characters outside the panel set",
     lambda d: d.add_hold_step(30.0, "Swap leads, now!", "Press CONT"),
     "ADD,HOLD,30.0,SWAP LEADS NOW,PRESS CONT"),
]


def _field_count(cmd: str) -> int:
    return len(cmd.split(","))


def main() -> int:
    failures = []
    for label, call, expected in CASES:
        d = _Capture()
        try:
            call(d)
            got = d.last()
        except Exception as exc:                           # noqa: BLE001
            failures.append((label, expected, f"raised {exc!r}"))
            continue
        if got != expected:
            failures.append((label, expected, got))

    # Field-count guard: the manual caps each step type's field count, and
    # exceeding it is ERR 6. This catches stray-comma regressions even if the
    # expected string above were ever updated carelessly.
    max_fields = {"ACW": 7, "DCW": 8, "IR": 8, "GB": 5,
                  "CONT": 4, "PAUSE": 2, "HOLD": 4}
    for label, call, expected in CASES:
        step_type = expected.split(",")[1]
        n = _field_count(expected) - 1          # drop the leading "ADD"
        if n > max_fields[step_type]:
            failures.append((label, f"<= {max_fields[step_type]} fields",
                             f"{n} fields — would be rejected as ERR 6"))

    for label, call, expected in CASES:
        print(f"{'FAIL' if any(f[0] == label for f in failures) else 'ok  '}  {label}")

    if failures:
        print(f"\n{len(failures)} failure(s):\n")
        for label, expected, got in failures:
            print(f"  {label}\n    expected: {expected}\n    got:      {got}\n")
        return 1
    print(f"\nAll {len(CASES)} ADD command cases match the manual field spec.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
