"""One-off, idempotent migration (2026-10-06): candle_indicators.ema5/14/21/50
-- the live chart's new EMA overlay lines (see db/models.py's CandleIndicators
and memory: live_indicators_phase1_priority). Needs the SSH tunnel to the
VM's SQL Server (see CLAUDE.md). Not needed for local SQLite dev -- a fresh
local_dev.db already gets these columns via Base.metadata.create_all(); an
existing one can just be deleted and recreated.

    .venv/Scripts/python.exe backend/scripts/migrate_ema_columns.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from sqlalchemy import text

from db.session import build_engine

STATEMENTS = (
    ("candle_indicators", "ema5", "ALTER TABLE candle_indicators ADD ema5 NUMERIC(9,2) NULL"),
    ("candle_indicators", "ema14", "ALTER TABLE candle_indicators ADD ema14 NUMERIC(9,2) NULL"),
    ("candle_indicators", "ema21", "ALTER TABLE candle_indicators ADD ema21 NUMERIC(9,2) NULL"),
    ("candle_indicators", "ema50", "ALTER TABLE candle_indicators ADD ema50 NUMERIC(9,2) NULL"),
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
