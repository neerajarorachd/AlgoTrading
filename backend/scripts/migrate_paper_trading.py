"""One-off, idempotent migration (2026-11-09, paper trading chunk 1):
creates the paper trading tables (db/models_paper.py). Changes no existing
table. Run against the live SQL Server BEFORE restarting on the new code.
Safe to re-run. Not needed for a fresh local SQLite file.

    .venv/Scripts/python.exe backend/scripts/migrate_paper_trading.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from sqlalchemy import inspect

from db.models_paper import (PaperBacktestMaster, PaperBacktestRun, PaperBacktestTrade,
                             PaperSelection, PaperTradeLive)
from db.session import build_engine

engine = build_engine()
present = set(inspect(engine).get_table_names())
for model in (PaperSelection, PaperTradeLive, PaperBacktestMaster, PaperBacktestRun, PaperBacktestTrade):
    name = model.__tablename__
    if name in present:
        print(f"{name}: already present, skipped")
        continue
    model.__table__.create(engine, checkfirst=True)
    print(f"{name}: created")
