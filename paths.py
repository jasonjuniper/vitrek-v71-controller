"""
paths.py
--------
Resolve where the app reads bundled read-only resources vs. where it writes
persistent per-machine data, correct whether the app runs from source or as a
PyInstaller **onefile** exe.

The onefile trap: `os.path.dirname(__file__)` inside the exe points at the
temporary `_MEIPASS` extraction dir, which Windows deletes when the process
exits. Writing the results DB (or the mutable PVD profiles) there means the data
evaporates on restart. So:

  * bundled_dir() — where the read-only resources shipped in the exe live
    (the _MEIPASS extract dir when frozen; the source tree otherwise).
  * data_dir()    — a writable directory that persists across restarts
    (C:\\ProgramData\\Juniper\\HiPotController when frozen; the source tree
    otherwise, so dev behaviour is unchanged).
"""
from __future__ import annotations

import os
import sys

APP_DATA_SUBDIR = os.path.join("Juniper", "HiPotController")


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundled_dir() -> str:
    """Directory of bundled read-only resources."""
    if _frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """Writable, persistent per-machine data directory (created if missing)."""
    if _frozen():
        base = os.environ.get("ProgramData", r"C:\ProgramData")
        d = os.path.join(base, APP_DATA_SUBDIR)
    else:
        d = os.path.dirname(os.path.abspath(__file__))
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d
