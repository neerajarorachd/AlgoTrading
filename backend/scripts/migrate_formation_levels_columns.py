"""One-off, idempotent migration (2026-10-05): instrument_activity.
neckline_price/stop_loss_price/target_price -- persists the real
measured-move geometry activity_engine.py already computes for double_top/
double_bottom/triple_top/triple_bottom at detection time (previously only
logged, never stored). Closes the gap flagged in [[watch_page_enhancements_plan]]:
the Market Watch buy/sell popup's expected_move.py can now read real
neckline-based SL/target for these 4 patterns instead of falling back to
ATR/backtested-range. NULL for every other pattern, and for any
graph-formation row detected before this column existed (no retroactive
backfill -- the swing-point state that produced an old row is long gone
from memory). Needs the SSH tunnel to the VM's SQL Server (see CLAUDE.md).

    .venv/Scripts/python.exe backend/scripts/migrate_formation_levels_columns.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from sqlalchemy import text

from db.session import build_engine

STATEMENTS = (
    ("instrument_activity", "neckline_price", "ALTER TABLE instrument_activity ADD neckline_price NUMERIC(18,4) NULL"),
    ("instrument_activity", "stop_loss_price", "ALTER TABLE instrument_activity ADD stop_loss_price NUMERIC(18,4) NULL"),
    ("instrument_activity", "target_price", "ALTER TABLE instrument_activity ADD target_price NUMERIC(18,4) NULL"),
)

engine = build_engine()
with engine.connect() as conn:
    for table, column, ddl in STATEMENTS:
        exists = conn.execute(text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME=:t AND COLUMN_NAME=:c"
        ), {"t": table, "c": column}).first()
        if exists:
            print(f"{table}.{column}: already present, skipped")
            continue
        conn.execute(text(ddl))
        print(f"{table}.{column}: added")
    conn.commit()
