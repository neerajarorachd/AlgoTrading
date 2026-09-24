"""One-off, idempotent migration for per-pattern defaults (2026-09-19). Adds:
  recommendation_systems.fallback_capital
  recommendations.suggested_quantity / suggested_sl_price / suggested_target_price
  recommendation_outcomes.first_hit / candles_to_hit   (only if that table already exists;
    otherwise the backend's create_all builds it with these columns)
The new table recommendation_system_pattern_defaults is created by the backend itself.
Needs the SSH tunnel to the VM's SQL Server (see CLAUDE.md).

    .venv/Scripts/python.exe backend/scripts/migrate_pattern_defaults_columns.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from sqlalchemy import text

from db.session import build_engine

COLUMNS = (
    ("recommendation_systems", "fallback_capital", "NUMERIC(18,2) NULL"),
    ("recommendations", "suggested_quantity", "INT NULL"),
    ("recommendations", "suggested_sl_price", "NUMERIC(18,4) NULL"),
    ("recommendations", "suggested_target_price", "NUMERIC(18,4) NULL"),
    ("recommendation_outcomes", "first_hit", "VARCHAR(10) NULL"),
    ("recommendation_outcomes", "candles_to_hit", "INT NULL"),
)

engine = build_engine()
with engine.connect() as conn:
    def scalar(sql, **params):
        return conn.execute(text(sql), params).first()

    for table, column, ddl in COLUMNS:
        if not scalar("SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME=:t", t=table):
            print(f"{table}: table not created yet, skipped ({column} will come with create_all)")
            continue
        if scalar("SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME=:t AND COLUMN_NAME=:c",
                  t=table, c=column):
            print(f"{table}.{column}: already present, skipped")
            continue
        conn.execute(text(f"ALTER TABLE {table} ADD {column} {ddl}"))
        print(f"{table}.{column}: added")
    conn.commit()
