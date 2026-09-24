"""One-off, idempotent migration for the RS1 rule gate (2026-09-19):
recommendation_systems.rule_combine_mode + recommendations.rule_note.
Needs the SSH tunnel to the VM's SQL Server (see CLAUDE.md).

    .venv/Scripts/python.exe backend/scripts/migrate_rule_gate_columns.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from sqlalchemy import text

from db.session import build_engine

STATEMENTS = (
    ("recommendation_systems", "rule_combine_mode",
     "ALTER TABLE recommendation_systems ADD rule_combine_mode VARCHAR(8) NOT NULL "
     "CONSTRAINT df_rs_rule_combine_mode DEFAULT 'all'"),
    ("recommendations", "rule_note", "ALTER TABLE recommendations ADD rule_note VARCHAR(160) NULL"),
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
