"""One-off, idempotent migration (2026-10-31, market story P2):
creates market_story_today and market_story_archive (db/models_story.py).

Run ONCE against the live SQL Server (SSH tunnel, see CLAUDE.md) BEFORE
switching the engine setting story_enabled to 1. The app also creates
missing tables at start (create_all), so this is the explicit, checked step;
it changes no existing table. Safe to re-run. Not needed for a fresh local
SQLite file.

    .venv/Scripts/python.exe backend/scripts/migrate_story_tables.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from sqlalchemy import inspect

from db.models_story import MarketStoryArchive, MarketStoryToday
from db.session import build_engine

engine = build_engine()
present = set(inspect(engine).get_table_names())
for model in (MarketStoryToday, MarketStoryArchive):
    name = model.__tablename__
    if name in present:
        print(f"{name}: already present, skipped")
        continue
    model.__table__.create(engine, checkfirst=True)
    print(f"{name}: created")
