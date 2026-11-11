"""One-off, idempotent migration (2026-11-11, manual trades, rulebook 3b):
creates trades / trade_indicator_snapshots / trade_accounts / trade_orders, and adds the
expiry columns to the paper trade tables when those already exist.
Run against the live SQL Server BEFORE restarting on the new code. Safe to
re-run. Not needed for a fresh local SQLite file.

    .venv/Scripts/python.exe backend/scripts/migrate_trades.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from sqlalchemy import inspect, text

from db.models_trades import Trade, TradeAccount, TradeIndicatorSnapshot, TradeOrder
from db.session import build_engine

engine = build_engine()
insp = inspect(engine)
present = set(insp.get_table_names())
for model in (Trade, TradeIndicatorSnapshot, TradeAccount, TradeOrder):
    name = model.__tablename__
    if name in present:
        print(f"{name}: already present, skipped")
        continue
    model.__table__.create(engine, checkfirst=True)
    print(f"{name}: created")

# columns added to trades after its first release
if "trades" in present:
    have = {c["name"] for c in insp.get_columns("trades")}
    with engine.begin() as conn:
        for col, typ in [("est_costs", "FLOAT"), ("exit_limit_price", "FLOAT")]:
            if col not in have:
                conn.execute(text(f"ALTER TABLE trades ADD {col} {typ} NULL"))
                print(f"trades.{col}: added")

bool_type = "BIT" if engine.dialect.name == "mssql" else "BOOLEAN"
expiry_cols = [("expiry_date", "DATE"), ("days_to_expiry", "INTEGER"),
               ("is_expiry_day", bool_type), ("is_expiry_week", bool_type)]
for table in ("paper_trades_live", "paper_backtest_trades"):
    if table not in present:
        print(f"{table}: not present (migrate_paper_trading.py creates it with the expiry columns)")
        continue
    have = {c["name"] for c in insp.get_columns(table)}
    with engine.begin() as conn:
        for col, typ in expiry_cols:
            if col in have:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD {col} {typ} NULL"))
            print(f"{table}.{col}: added")
print("done")
