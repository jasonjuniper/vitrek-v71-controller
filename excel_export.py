"""
excel_export.py
---------------
Export test session(s) from SQLite to a formatted Excel workbook.

Each session gets its own sheet. A summary sheet lists all sessions.
The workbook is intended for SharePoint sync.
"""

import os
import datetime
from typing import Optional

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from database import get_sessions, get_session, get_steps, DB_PATH

# Color palette
GREEN  = "FF92D050"   # pass
RED    = "FFFF0000"   # fail
YELLOW = "FFFFFF00"   # warning / incomplete
HEADER = "FF1F4E79"   # dark blue
SUBHDR = "FF2E75B6"   # mid blue
WHITE  = "FFFFFFFF"

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


def _hdr_cell(ws, row, col, value, bold=True, color=HEADER, font_color=WHITE, wrap=False):
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(bold=bold, color=font_color, name="Calibri")
    c.fill = PatternFill("solid", fgColor=color)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=wrap)
    return c


def _data_cell(ws, row, col, value, number_format=None, bold=False, fill_color=None):
    c = ws.cell(row=row, column=col, value=value)
    c.font = Font(bold=bold, name="Calibri")
    c.alignment = Alignment(vertical="center")
    if number_format:
        c.number_format = number_format
    if fill_color:
        c.fill = PatternFill("solid", fgColor=fill_color)
    return c


def _thin_border():
    s = Side(style="thin", color="FF999999")
    return Border(left=s, right=s, top=s, bottom=s)


def decode_flags(flags: int) -> str:
    if flags == 0:
        return "PASS"
    msgs = [desc for bit, desc in STATUS_FLAGS.items() if flags & bit]
    return "; ".join(msgs) if msgs else f"Unknown (0x{flags:X})"


def _write_session_sheet(wb: openpyxl.Workbook, session: dict, steps: list[dict]) -> None:
    title = f"S{session['id']}-{(session.get('serial_number') or 'DUT')[:20]}"
    ws = wb.create_sheet(title=title)
    ws.sheet_view.showGridLines = True
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 14
    ws.column_dimensions["H"].width = 30

    # --- Session header block ---
    ws.merge_cells("A1:H1")
    t = ws["A1"]
    t.value = "VITREK V71 HiPot Test Report"
    t.font = Font(bold=True, size=14, color=WHITE, name="Calibri")
    t.fill = PatternFill("solid", fgColor=HEADER)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    meta = [
        ("Session ID",     session["id"]),
        ("Started",        session.get("started_at", "")),
        ("Finished",       session.get("finished_at", "") or "—"),
        ("Operator",       session.get("operator", "") or "—"),
        ("Part Number",    session.get("part_number", "") or "—"),
        ("DUT Serial",     session.get("serial_number", "") or "—"),
        ("Device Model",   session.get("device_model", "") or "—"),
        ("Device Serial",  session.get("device_serial", "") or "—"),
        ("Firmware",       session.get("firmware", "") or "—"),
        ("Notes",          session.get("notes", "") or "—"),
    ]
    for i, (label, value) in enumerate(meta, start=2):
        _hdr_cell(ws, i, 1, label, color=SUBHDR, bold=True)
        ws.cell(row=i, column=2, value=str(value)).alignment = Alignment(vertical="center")

    # Overall result
    overall = session.get("overall_result")
    passed = session.get("passed")
    result_row = 2 + len(meta)
    ws.merge_cells(f"A{result_row}:H{result_row}")
    rc = ws[f"A{result_row}"]
    if passed == 1:
        rc.value = "✓  OVERALL: PASS"
        rc.fill = PatternFill("solid", fgColor=GREEN)
    elif passed == 0:
        rc.value = f"✗  OVERALL: FAIL  — {decode_flags(overall or 0)}"
        rc.fill = PatternFill("solid", fgColor=RED)
    else:
        rc.value = "—  INCOMPLETE"
        rc.fill = PatternFill("solid", fgColor=YELLOW)
    rc.font = Font(bold=True, size=12, name="Calibri")
    rc.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[result_row].height = 22

    # --- Steps table ---
    tbl_start = result_row + 2
    headers = ["Step #", "Type", "Phase", "Elapsed (s)",
               "Level (V/A)", "Leakage/R", "Arc (A)", "Status / Flags"]
    for col, h in enumerate(headers, 1):
        _hdr_cell(ws, tbl_start, col, h, color=SUBHDR, wrap=True)
    ws.row_dimensions[tbl_start].height = 30

    for r, step in enumerate(steps, start=tbl_start + 1):
        sf = step.get("status_flags", 0) or 0
        row_color = None
        if step.get("passed") == 1:
            row_color = "FFE2EFDA"
        elif step.get("passed") == 0:
            row_color = "FFFFC7CE"

        _data_cell(ws, r, 1, step.get("step_number"), fill_color=row_color)
        _data_cell(ws, r, 2, step.get("step_type", ""), fill_color=row_color)
        _data_cell(ws, r, 3, step.get("phase", ""), fill_color=row_color)
        _data_cell(ws, r, 4, step.get("elapsed_s"), number_format="0.000", fill_color=row_color)
        _data_cell(ws, r, 5, step.get("level"), number_format="0.000E+00", fill_color=row_color)
        _data_cell(ws, r, 6, step.get("measurement"), number_format="0.000E+00", fill_color=row_color)
        _data_cell(ws, r, 7, step.get("arc_a"), number_format="0.000E+00", fill_color=row_color)
        _data_cell(ws, r, 8, decode_flags(sf), fill_color=row_color)

        for col in range(1, 9):
            ws.cell(row=r, column=col).border = _thin_border()


def _write_summary_sheet(wb: openpyxl.Workbook, sessions: list[dict]) -> None:
    ws = wb.active
    ws.title = "Summary"
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 18
    ws.column_dimensions["F"].width = 12
    ws.column_dimensions["G"].width = 14

    ws.merge_cells("A1:G1")
    t = ws["A1"]
    t.value = "VITREK V71 — Test Session Summary"
    t.font = Font(bold=True, size=14, color=WHITE, name="Calibri")
    t.fill = PatternFill("solid", fgColor=HEADER)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    hdrs = ["Session ID", "Started", "Finished", "Part Number", "DUT Serial", "Result", "Steps"]
    for col, h in enumerate(hdrs, 1):
        _hdr_cell(ws, 2, col, h, color=SUBHDR)

    for r, s in enumerate(sessions, start=3):
        passed = s.get("passed")
        row_color = "FFE2EFDA" if passed == 1 else ("FFFFC7CE" if passed == 0 else None)
        result_str = "PASS" if passed == 1 else ("FAIL" if passed == 0 else "—")

        _data_cell(ws, r, 1, s.get("id"), fill_color=row_color)
        _data_cell(ws, r, 2, s.get("started_at", ""), fill_color=row_color)
        _data_cell(ws, r, 3, s.get("finished_at", "") or "—", fill_color=row_color)
        _data_cell(ws, r, 4, s.get("part_number", "") or "—", fill_color=row_color)
        _data_cell(ws, r, 5, s.get("serial_number", "") or "—", fill_color=row_color)
        _data_cell(ws, r, 6, result_str, bold=True, fill_color=row_color)
        _data_cell(ws, r, 7, None, fill_color=row_color)  # step count filled below

        for col in range(1, 8):
            ws.cell(row=r, column=col).border = _thin_border()

    # Auto-filter on the header row
    ws.auto_filter.ref = f"A2:G{2 + len(sessions)}"


def export_to_excel(output_path: str,
                    session_ids: Optional[list[int]] = None,
                    db_path: str = DB_PATH) -> str:
    """
    Export sessions to an Excel file.
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
