"""Explains every pattern hit of one session: for each hit, every condition
of the pattern with its required value and the actual value on that candle.

READ-ONLY on the real DB: candles, pattern definitions and strength rules
are read from it; the engine runs on a throwaway in-memory SQLite database.

    .venv/Scripts/python.exe backend/scripts/explain_patterns_day.py [--day 2026-10-16] [--symbols HINDCOPPER] [--out hits.json]

Built 2026-10-16 for the pattern review ("too many occurrences"): the user
goes through the patterns one by one with this table.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine
from db.models import SubscribedSymbol
from db.session import build_engine, build_session_factory, session_scope
from feed.candle_aggregator import CandleAggregator
from pattern_engine import PatternEvaluator
from replay_patterns_day import IST, ist_day, load_candles, scratch_db

def run(scratch, live, symbol, segment, candles, day):
    engine = ActivityEngine(scratch)
    evaluator = PatternEvaluator(live, owner_id=1)  # live definitions + strength rules, read-only
    engine.pattern_evaluator = evaluator

    def on_closed(sym, seg, c):
        if ist_day(c.timestamp) < day:
            engine.warm_up(sym, seg, c)
        else:
            engine.on_candle_closed(sym, seg, c)

    agg = CandleAggregator(on_candle_closed=on_closed)
    for c in candles:
        agg.ingest_historical_1min(symbol, segment, c)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) - IST
    agg.flush_all(as_of=min(end, datetime.now(timezone.utc)))
    # the engine records why each hit formed (hit_explain) -- the same numbers /api/review serves
    out = []
    for key, entries in evaluator.explanations.items():
        for e in entries:
            if ist_day(e["ts"]) == day:
                out.append({**{k: v for k, v in e.items() if k != "ts"}, "symbol": key[0], "time": (e["ts"] + IST).strftime("%H:%M")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None, help="IST date, default today")
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--out", default=None, help="write the hits as JSON here")
    args = ap.parse_args()
    live = build_session_factory(build_engine())
    with session_scope(live) as s:
        symbols = [(r.symbol, r.exchange_segment) for r in s.query(SubscribedSymbol).filter_by(active=True).order_by(SubscribedSymbol.symbol)]
    if args.symbols:
        wanted = set(args.symbols.split(","))
        symbols = [x for x in symbols if x[0] in wanted]
    day = date.fromisoformat(args.day) if args.day else (datetime.now(timezone.utc) + IST).date()
    scratch = scratch_db(symbols)
    hits = []
    for symbol, segment in symbols:
        candles = load_candles(live, symbol, segment, day)
        mine = run(scratch, live, symbol, segment, candles, day)
        print(f"{symbol:12} {len(mine):4} hits", flush=True)
        hits += mine
    print(f"\n{day}: {len(hits)} pattern hits on {len(symbols)} stocks")
    if args.out:
        Path(args.out).write_text(json.dumps({"day": str(day), "hits": hits}, default=str, indent=1), encoding="utf-8")
        print("written", args.out)


if __name__ == "__main__":
    main()
