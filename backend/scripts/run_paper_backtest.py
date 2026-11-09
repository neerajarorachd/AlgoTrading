"""Runs one paper backtest comparison (master) in its own process, so it
never slows the live app. Started by POST /api/paper/backtests; can also be
run by hand to re-run a master:

    .venv/Scripts/python.exe backend/scripts/run_paper_backtest.py --master 3
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.models_paper import PaperBacktestTrade, PaperBacktestRun
from db.session import build_engine, build_session_factory
from paper.backtest_runner import run_master

logging.basicConfig(level=logging.INFO)
ap = argparse.ArgumentParser()
ap.add_argument("--master", type=int, required=True)
args = ap.parse_args()
factory = build_session_factory(build_engine())
with factory() as s:  # a re-run starts clean
    run_ids = [r.id for r in s.query(PaperBacktestRun.id).filter_by(master_id=args.master)]
    s.query(PaperBacktestTrade).filter(PaperBacktestTrade.run_id.in_(run_ids)).delete(synchronize_session=False)
    s.commit()
run_master(factory, args.master)
