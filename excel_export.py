"""
excel_export.py
---------------
Export test session(s) from SQLite to a Juniper-branded Excel workbook.

Each session gets its own sheet. A summary sheet lists all sessions.
The workbook is intended for SharePoint sync.

Brand palette mirrors juniper-brand.css light-theme tokens:
  Navy    #1a1a2e  — brand bar / main headers
  Primary #1565c0  — sub-headers / accents
  Accent  #6e8cff  — highlight trim
  Offwhite #e8e7e3 — logo plate background
  Pass    #2e7d32 on #e8f5e9
  Fail    #b71c1c on #ffebee
  Muted   #f5f5f5 — alternating rows / meta labels
"""

import os
import datetime
from typing import Optional

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, GradientFill
from openpyxl.drawing.image import Image as XLImage
from openpyxl.utils import get_column_letter

from database import get_sessions, get_session, get_steps, DB_PATH

# ── Juniper brand palette (ARGB hex, openpyxl format) ─────────────────────────
J_NAVY        = "FF1A1A2E"   # brand bar / primary headers
J_PRIMARY     = "FF1565C0"   # sub-headers
J_ACCENT      = "FF6E8CFF"   # trim / borders
J_OFFWHITE    = "FFE8E7E3"   # light plate / alternating rows
J_CHARCOAL    = "FF3C3C3D"   # secondary text
J_WHITE       = "FFFFFFFF"

# Data-row semantic colours
PASS_BG       = "FFE8F5E9"   # light green
PASS_FG       = "FF2E7D32"
FAIL_BG       = "FFFFEBEE"   # light red
FAIL_FG       = "FFB71C1C"
INCOMPLETE_BG = "FFFFF8E1"   # light amber
INCOMPLETE_FG = "FFF57F17"

# Meta label column
META_BG       = "FFF0F2F5"   # --bg equivalent

# ── Font name: Calibri is the closest Excel built-in to Poppins geometry ──────
BRAND_FONT = "Calibri"

STATUS_FLAGS = {
    1:     "Internal fault",
    2:     "Over voltage",
    4:     "Line too low",
    8:     "DUT breakdown",
    16:    "HOLD timeout",
    32:    "User aborted",
    64:    "GB over-compliance",
    128:   "Arc detected",
    256:   "Below min limit",
    512:   "Above max limit",
    1024:  "IR steady current fail",
    2048:  "Interlock failure",
    4096:  "Switch matrix error",
    8192:  "Overheated",
    16384: "Cannot control output",
    32768: "GB wiring error",
    65536: "Drive instability",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _border(color="FFD0D7E3", style="thin"):
    s = Side(style=style, color=color)
    return Border(left=s, right=s, top=s, bottom=s)

def _accent_border():
    """Left-only accent bar in J_ACCENT for meta label column."""
    return Border(left=Side(style="medium", color=J_ACCENT))

def _navy_cell(ws, row, col, value, size=11, bold=True, span_end_col=None):
    """Dark navy header cell — used for sheet title banners."""
    if span_end_col:
        ws.merge_cells(start_row=row, start_column=col,
                       end_row=row, end_column=span_end_col)
    c = ws.cell(row=row, column=col, value=value)
    c.font      = Font(name=BRAND_FONT, bold=bold, size=size, color=J_WHITE)
    c.fill      = PatternFill("solid", fgColor=J_NAVY)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=2)
    return c

def _primary_cell(ws, row, col, value, size=9, bold=True, wrap=False, span_end_col=None):
    """Mid-blue sub-header cell."""
    if span_end_col:
        ws.merge_cells(start_row=row, start_column=col,
                       end_row=row, end_column=span_end_col)
    c = ws.cell(row=row, column=col, value=value)
    c.font      = Font(name=BRAND_FONT, bold=bold, size=size, color=J_WHITE)
    c.fill      = PatternFill("solid", fgColor=J_PRIMARY)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=wrap)
    return c

def _meta_label(ws, row, col, value):
    """Label cell for the session meta block (left column)."""
    c = ws.cell(row=row, column=col, value=value)
    c.font      = Font(name=BRAND_FONT, bold=True, size=9, color=J_CHARCOAL)
    c.fill      = PatternFill("solid", fgColor=META_BG)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=2)
    c.border    = _accent_border()
    return c

def _meta_value(ws, row, col, value):
    """Value cell for the session meta block (right column)."""
    c = ws.cell(row=row, column=col, value=str(value) if value is not None else "—")
    c.font      = Font(name=BRAND_FONT, size=9, color=J_CHARCOAL)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=2)
    c.border    = _border(color="FFE0E0E0")
    return c

def _data_cell(ws, row, col, value, number_format=None, bold=False,
               bg=None, fg=J_CHARCOAL, size=9):
    c = ws.cell(row=row, column=col, value=value)
    c.font      = Font(name=BRAND_FONT, bold=bold, size=size, color=fg)
    c.alignment = Alignment(vertical="center", indent=1)
    c.border    = _border()
    if number_format:
        c.number_format = number_format
    if bg:
        c.fill = PatternFill("solid", fgColor=bg)
    return c

def decode_flags(flags: int) -> str:
    if flags == 0:
        return "PASS"
    msgs = [desc for bit, desc in STATUS_FLAGS.items() if flags & bit]
    return "; ".join(msgs) if msgs else f"Unknown (0x{flags:X})"


# ── Branded title banner (rows 1–2) ───────────────────────────────────────────

def _write_title_banner(ws, num_cols: int, subtitle: str) -> int:
    """
    Write a two-row Juniper brand header spanning all columns.
    Returns the next available row number.
    """
    # Row 1: navy brand bar — "JUNIPER DESIGN  ·  V71 HiPot Controller"
    c1 = _navy_cell(ws, 1, 1, "JUNIPER DESIGN  ·  V71 HiPot Controller",
                    size=13, span_end_col=num_cols)
    ws.row_dimensions[1].height = 30

    # Right-align a "Generated" timestamp in the same merged cell's last physical cell
    ts_cell = ws.cell(row=1, column=num_cols,
                      value=f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M}")
    ts_cell.font      = Font(name=BRAND_FONT, size=8, color="FFAAAACC", italic=True)
    ts_cell.fill      = PatternFill("solid", fgColor=J_NAVY)
    ts_cell.alignment = Alignment(horizontal="right", vertical="center", indent=2)

    # Row 2: accent-coloured subtitle bar
    c2 = ws.cell(row=2, column=1, value=subtitle)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=num_cols)
    c2.font      = Font(name=BRAND_FONT, bold=True, size=10, color=J_WHITE)
    c2.fill      = PatternFill("solid", fgColor=J_PRIMARY)
    c2.alignment = Alignment(horizontal="left", vertical="center", indent=3)
    ws.row_dimensions[2].height = 20

    return 3  # next free row


# ── Session detail sheet ───────────────────────────────────────────────────────

def _write_session_sheet(wb: openpyxl.Workbook, session: dict, steps: list[dict]) -> None:
    title = f"S{session['id']}-{(session.get('serial_number') or 'DUT')[:18]}"
    ws = wb.create_sheet(title=title)
    ws.sheet_view.showGridLines = False

    # Column widths
    col_widths = [22, 32, 14, 14, 16, 16, 14, 34]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    NUM_COLS = len(col_widths)

    # Title banner
    next_row = _write_title_banner(
        ws, NUM_COLS,
        subtitle=f"Test Report  ·  Session #{session['id']}  ·  "
                 f"DUT: {session.get('serial_number') or '—'}  ·  "
                 f"Part: {session.get('part_number') or '—'}"
    )

    # ── Meta block ──────────────────────────────────────────────────────────
    meta = [
        ("Session ID",    session["id"]),
        ("Started",       session.get("started_at", "")),
        ("Finished",      session.get("finished_at") or "—"),
        ("Operator",      session.get("operator") or "—"),
        ("Part Number",   session.get("part_number") or "—"),
        ("DUT Serial",    session.get("serial_number") or "—"),
        ("Device Model",  session.get("device_model") or "—"),
        ("Device Serial", session.get("device_serial") or "—"),
        ("Firmware",      session.get("firmware") or "—"),
        ("Notes",         session.get("notes") or "—"),
    ]
    for label, value in meta:
        _meta_label(ws, next_row, 1, label)
        _meta_value(ws, next_row, 2, value)
        # Shade remaining columns in the meta rows for a clean look
        for col in range(3, NUM_COLS + 1):
            c = ws.cell(row=next_row, column=col)
            c.fill = PatternFill("solid", fgColor=J_OFFWHITE)
        ws.row_dimensions[next_row].height = 16
        next_row += 1

    # ── Overall result banner ───────────────────────────────────────────────
    overall = session.get("overall_result")
    passed  = session.get("passed")
    next_row += 1   # blank spacer
    ws.merge_cells(start_row=next_row, start_column=1,
                   end_row=next_row, end_column=NUM_COLS)
    rc = ws.cell(row=next_row, column=1)
    if passed == 1:
        rc.value = "✓   OVERALL RESULT:   PASS"
        rc.font  = Font(name=BRAND_FONT, bold=True, size=13, color=J_WHITE)
        rc.fill  = PatternFill("solid", fgColor="FF2E7D32")
    elif passed == 0:
        rc.value = f"✗   OVERALL RESULT:   FAIL  —  {decode_flags(overall or 0)}"
        rc.font  = Font(name=BRAND_FONT, bold=True, size=13, color=J_WHITE)
        rc.fill  = PatternFill("solid", fgColor="FFB71C1C")
    else:
        rc.value = "—   INCOMPLETE / IN PROGRESS"
        rc.font  = Font(name=BRAND_FONT, bold=True, size=13, color=INCOMPLETE_FG)
        rc.fill  = PatternFill("solid", fgColor=INCOMPLETE_BG)
    rc.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[next_row].height = 26
    next_row += 2   # spacer before steps table

    # ── Steps table ─────────────────────────────────────────────────────────
    step_headers = ["Step #", "Type", "Phase", "Elapsed (s)",
                    "Level (V/A)", "Leakage / R", "Arc (A)", "Status / Flags"]
    for col, h in enumerate(step_headers, 1):
        _primary_cell(ws, next_row, col, h, wrap=True)
    ws.row_dimensions[next_row].height = 32
    next_row += 1

    for step in steps:
        sf    = step.get("status_flags", 0) or 0
        p     = step.get("passed")
        bg    = PASS_BG if p == 1 else (FAIL_BG if p == 0 else None)
        fg    = PASS_FG if p == 1 else (FAIL_FG if p == 0 else J_CHARCOAL)

        _data_cell(ws, next_row, 1, step.get("step_number"),   bg=bg, bold=True, fg=fg)
        _data_cell(ws, next_row, 2, step.get("step_type", ""), bg=bg, bold=True, fg=fg)
        _data_cell(ws, next_row, 3, step.get("phase", ""),     bg=bg, fg=fg)
        _data_cell(ws, next_row, 4, step.get("elapsed_s"),     bg=bg, fg=fg, number_format="0.000")
        _data_cell(ws, next_row, 5, step.get("level"),         bg=bg, fg=fg, number_format="0.000E+00")
        _data_cell(ws, next_row, 6, step.get("measurement"),   bg=bg, fg=fg, number_format="0.000E+00")
        _data_cell(ws, next_row, 7, step.get("arc_a"),         bg=bg, fg=fg, number_format="0.000E+00")
        _data_cell(ws, next_row, 8, decode_flags(sf),          bg=bg, fg=fg, bold=(sf != 0))
        ws.row_dimensions[next_row].height = 15
        next_row += 1

    # ── Juniper footer rule ─────────────────────────────────────────────────
    next_row += 1
    ws.merge_cells(start_row=next_row, start_column=1,
                   end_row=next_row, end_column=NUM_COLS)
    ft = ws.cell(row=next_row, column=1,
                 value="Designed and built by Juniper Design  ·  juniperdesign.com")
    ft.font      = Font(name=BRAND_FONT, size=8, italic=True, color="FF9090A8")
    ft.fill      = PatternFill("solid", fgColor=J_NAVY)
    ft.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[next_row].height = 18


# ── Summary sheet ─────────────────────────────────────────────────────────────

def _write_summary_sheet(wb: openpyxl.Workbook, sessions: list[dict]) -> None:
    ws = wb.active
    ws.title = "Summary"
    ws.sheet_view.showGridLines = False

    col_widths = [10, 22, 22, 20, 20, 10, 10]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    NUM_COLS = len(col_widths)

    # Stats for subtitle
    total  = len(sessions)
    passed = sum(1 for s in sessions if s.get("passed") == 1)
    failed = total - passed
    next_row = _write_title_banner(
        ws, NUM_COLS,
        subtitle=f"All Test Sessions  ·  {total} total  ·  {passed} passed  ·  {failed} failed"
    )

    # Column headers
    hdrs = ["Session #", "Started", "Finished", "Part Number", "DUT Serial", "Result", "Steps"]
    for col, h in enumerate(hdrs, 1):
        _primary_cell(ws, next_row, col, h)
    ws.row_dimensions[next_row].height = 20
    next_row += 1

    for s in sessions:
        p = s.get("passed")
        bg         = PASS_BG if p == 1 else (FAIL_BG if p == 0 else None)
        fg         = PASS_FG if p == 1 else (FAIL_FG if p == 0 else J_CHARCOAL)
        result_str = "PASS"    if p == 1 else ("FAIL" if p == 0 else "—")

        _data_cell(ws, next_row, 1, s.get("id"),                     bg=bg, fg=fg, bold=True)
        _data_cell(ws, next_row, 2, s.get("started_at", ""),         bg=bg, fg=fg)
        _data_cell(ws, next_row, 3, s.get("finished_at") or "—",     bg=bg, fg=fg)
        _data_cell(ws, next_row, 4, s.get("part_number") or "—",     bg=bg, fg=fg)
        _data_cell(ws, next_row, 5, s.get("serial_number") or "—",   bg=bg, fg=fg)
        _data_cell(ws, next_row, 6, result_str, bg=bg, fg=fg, bold=True)
        _data_cell(ws, next_row, 7, None, bg=bg, fg=fg)
        ws.row_dimensions[next_row].height = 15
        next_row += 1

    # Auto-filter on the header row (row 3 = next_row - len(sessions) - 1)
    hdr_row = 3
    ws.auto_filter.ref = (
        f"A{hdr_row}:{get_column_letter(NUM_COLS)}{hdr_row + len(sessions)}"
    )

    # Footer
    next_row += 1
    ws.merge_cells(start_row=next_row, start_column=1,
                   end_row=next_row, end_column=NUM_COLS)
    ft = ws.cell(row=next_row, column=1,
                 value="Designed and built by Juniper Design  ·  juniperdesign.com")
    ft.font      = Font(name=BRAND_FONT, size=8, italic=True, color="FF9090A8")
    ft.fill      = PatternFill("solid", fgColor=J_NAVY)
    ft.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[next_row].height = 18


# ── Public API ────────────────────────────────────────────────────────────────

def export_to_excel(output_path: str,
                    session_ids: Optional[list[int]] = None,
                    db_path: str = DB_PATH) -> str:
    """
    Export sessions to a Juniper-branded Excel file.
    If session_ids is None, exports all sessions (most recent first, up to 500).
    Returns the path to the written file.
    """
    if session_ids:
        sessions = [get_session(sid, db_path) for sid in session_ids]
        sessions = [s for s in sessions if s]
    else:
        sessions = get_sessions(limit=500, db_path=db_path)

    wb = openpyxl.Workbook()
    _write_summary_sheet(wb, sessions)

    for session in sessions:
        steps = get_steps(session["id"], db_path)
        _write_session_sheet(wb, session, steps)

    wb.save(output_path)
    return output_path


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "hipot_results.xlsx"
    path = export_to_excel(out)
    print(f"Exported to {path}")
