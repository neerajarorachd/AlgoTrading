"""One-off, idempotent migration (2026-10-03): strategies.swing_lookback --
per-strategy override of ActivityEngine's swing_lookback (candles on each
side confirming a swing high/low), so a backtest run can set/sweep it (3, 5,
7...) instead of only ever reading the one global engine_settings row. See
db/models.py's Strategy.swing_lookback and order_backtest.py's
engine_config_from_strategy. Needs the SSH tunnel to the VM's SQL Server
(see CLAUDE.md).

    .venv/Scripts/python.exe backend/scripts/migrate_swing_lookback_column.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from sqlalchemy import text

from db.session import build_engine

STATEMENTS = (
    ("strategies", "swing_lookback", "ALTER TABLE strategies ADD swing_lookback INT NULL"),
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
