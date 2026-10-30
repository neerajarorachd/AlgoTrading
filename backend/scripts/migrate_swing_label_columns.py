"""One-off, idempotent migration (2026-10-30, market story P1):
instrument_activity.leg_pct / swing_ratio -- the leg % and swing ratio stored
on the swing label rows (swing_hh, swing_hl, ...).

Run ONCE against the live SQL Server (SSH tunnel, see CLAUDE.md) BEFORE
restarting the app on code that has these columns -- the engine's bulk save
writes every column of InstrumentActivity, so without them EVERY activity
flush would fail, not just the new rows. Safe to re-run. Not needed for a
fresh local SQLite file (create_all adds them).

    .venv/Scripts/python.exe backend/scripts/migrate_swing_label_columns.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from sqlalchemy import inspect, text

from db.session import build_engine

COLUMNS = (
    ("instrument_activity", "leg_pct", "NUMERIC(9,4) NULL"),
    ("instrument_activity", "swing_ratio", "NUMERIC(9,4) NULL"),
)

engine = build_engine()
with engine.connect() as conn:
    for table, column, ddl in COLUMNS:
        present = {c["name"] for c in inspect(conn).get_columns(table)}
        if column in present:
            print(f"{table}.{column}: already present, skipped")
            continue
        conn.execute(text(f"ALTER TABLE {table} ADD {column} {ddl}"))
        print(f"{table}.{column}: added")
    conn.commit()
