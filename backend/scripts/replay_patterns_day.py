"""Replays one past session through the activity engine with pattern
evaluation on (ALGO-32) and reports what the patterns would have marked.

READ-ONLY on the real DB: candles are read from it; patterns, strength rules
and every engine write go to a throwaway in-memory SQLite database.

    .venv/Scripts/python.exe backend/scripts/replay_patterns_day.py [--day 2026-10-13] [--symbols BANKBARODA,TCS]

Also checks restart consistency: a run that "restarts" at 12:00 the way
recovery does (history warm-up, today's recorded part warmed with pattern
state, then live) must record exactly the afternoon hits a straight run does.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from activity_engine import ActivityEngine
from brokers.models import Candle
from db.models import Base, SubscribedSymbol
from db.ops import LibCandles, LibCandlesHistorical
from db.ops.LibPatterns import seed_system_patterns
from db.ops.LibSignalStrength import seed_defaults as seed_strength
from db.ops.LibSystemSettings import seed_defaults as seed_settings
from db.session import build_engine, build_session_factory, session_scope
from feed.candle_aggregator import CandleAggregator
from pattern_engine import PatternEvaluator
from patterns_catalog import PATTERN_SETTING_SEED, SYSTEM_PATTERNS
from signal_strength_defaults import SIGNAL_STRENGTH_BASE, STRENGTH_SETTING_SEED

IST = timedelta(hours=5, minutes=30)
WARMUP_DAYS = 6


def ist_day(ts):
    return (ts + IST).date()


def load_candles(live, symbol, segment, day: date):
    start = datetime.combine(day - timedelta(days=WARMUP_DAYS), datetime.min.time())
    end = datetime.combine(day + timedelta(days=1), datetime.min.time()) - IST
    by_ts = {}
    with session_scope(live) as s:
        for ops in (LibCandles, LibCandlesHistorical):
            for r in ops.get_range(s, symbol, segment, "1min", start, end):
                ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
                by_ts[ts] = Candle(symbol=symbol, timeframe="1min", timestamp=ts, open=float(r.open_price),
                                   high=float(r.high_price), low=float(r.low_price), close=float(r.close_price),
                                   volume=int(r.volume or 0))
    return [by_ts[t] for t in sorted(by_ts) if ist_day(t) <= day]


def scratch_db(symbols):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng)
    seed_settings(factory, PATTERN_SETTING_SEED)
    seed_settings(factory, STRENGTH_SETTING_SEED)
    seed_system_patterns(factory, SYSTEM_PATTERNS)
    seed_strength(factory, SIGNAL_STRENGTH_BASE)
    with factory() as s:
        for sym, seg in symbols:
            s.add(SubscribedSymbol(symbol=sym, exchange="NSE", segment="EQUITY", exchange_segment=seg, security_id="0", previous_close=0))
        s.commit()
    return factory


def run(factory, symbol, segment, candles, day, restart_at=None):
    """Returns (pattern hits on `day`, raw events on `day`, seconds in pattern eval, candle closes on `day`)."""
    timing = {"eval": 0.0, "closes": 0}

    def fresh_engine():
        engine = ActivityEngine(factory)
        evaluator = PatternEvaluator(factory, owner_id=None)
        original = evaluator.on_candle

        def timed(*a, **k):
            t = time.perf_counter()
            try:
                return original(*a, **k)
            finally:
                timing["eval"] += time.perf_counter() - t
                timing["closes"] += 1
        evaluator.on_candle = timed
        engine.pattern_evaluator = evaluator
        return engine

    engine = fresh_engine()
    phase = {"live": True}

    def on_closed(sym, seg, c):
        if ist_day(c.timestamp) < day:
            engine.warm_up(sym, seg, c)
        elif phase["live"]:
            engine.on_candle_closed(sym, seg, c)
        else:  # after a simulated restart: today's recorded part, pattern state only
            engine.warm_up(sym, seg, c, evaluate_patterns=True)

    def feed(eng_candles, as_of):
        agg = CandleAggregator(on_candle_closed=on_closed)
        for c in eng_candles:
            agg.ingest_historical_1min(symbol, segment, c)
        agg.flush_all(as_of=as_of)

    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) - IST
    if restart_at is None:
        feed(candles, end)
        rows = list(engine._buffer)
    else:
        # restart: new engine; history + today's part before restart_at without recording
        engine = fresh_engine()
        phase["live"] = False
        before = [c for c in candles if c.timestamp < restart_at]
        after = [c for c in candles if c.timestamp >= restart_at]
        agg = CandleAggregator(on_candle_closed=on_closed)
        for c in before:
            agg.ingest_historical_1min(symbol, segment, c)
        phase["live"] = True
        for c in after:
            agg.ingest_historical_1min(symbol, segment, c)
        agg.flush_all(as_of=end)
        rows = list(engine._buffer)
    on_day = [r for r in rows if ist_day(r["ts"]) == day]
    hits = [r for r in on_day if r["activity_type"] == "pattern"]
    raw = [r for r in on_day if r["activity_type"] != "pattern"]
    return hits, raw, timing


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None, help="IST date, default: the last session before today")
    ap.add_argument("--symbols", default=None)
    args = ap.parse_args()
    live = build_session_factory(build_engine())
    with session_scope(live) as s:
        symbols = [(r.symbol, r.exchange_segment) for r in s.query(SubscribedSymbol).filter_by(active=True).order_by(SubscribedSymbol.symbol)]
    if args.symbols:
        wanted = set(args.symbols.split(","))
        symbols = [x for x in symbols if x[0] in wanted]
    day = date.fromisoformat(args.day) if args.day else None
    factory = scratch_db(symbols)

    per_pattern = Counter()
    per_tf = Counter()
    raw_total = hits_total = closes = 0
    eval_seconds = 0.0
    restart_ok = True
    for symbol, segment in symbols:
        candles = load_candles(live, symbol, segment, day or (datetime.now(timezone.utc) + IST).date() - timedelta(days=1))
        if day is None:
            day = max(ist_day(c.timestamp) for c in candles)
            candles = [c for c in candles if ist_day(c.timestamp) <= day]
        hits, raw, timing = run(factory, symbol, segment, candles, day)
        restart_at = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12) - IST
        hits2, _, _ = run(factory, symbol, segment, candles, day, restart_at=restart_at)
        straight_pm = sorted((h["timeframe"], h["ts"], h["activity"]) for h in hits if h["ts"] >= restart_at)
        restarted_pm = sorted((h["timeframe"], h["ts"], h["activity"]) for h in hits2 if h["ts"] >= restart_at)
        same = straight_pm == restarted_pm
        restart_ok &= same
        for h in hits:
            per_pattern[h["activity"]] += 1
            per_tf[h["timeframe"]] += 1
        raw_total += len(raw)
        hits_total += len(hits)
        closes += timing["closes"]
        eval_seconds += timing["eval"]
        print(f"{symbol:12} raw events {len(raw):5}  pattern hits {len(hits):4}  "
              f"afternoon after restart {'same' if same else 'DIFFERENT'} ({len(straight_pm)} vs {len(restarted_pm)})", flush=True)

    print(f"\nDay {day}: {len(symbols)} stocks, raw events {raw_total}, pattern hits {hits_total} "
          f"({hits_total / max(raw_total, 1):.1%} of raw)")
    print(f"Pattern evaluation: {closes} candle closes, {eval_seconds:.1f} s total, "
          f"{1000 * eval_seconds / max(closes, 1):.1f} ms per candle close")
    print("Hits by timeframe:", dict(per_tf))
    print("Hits by pattern:")
    for k, n in per_pattern.most_common():
        print(f"  {k:30} {n}")
    print("Restart consistency:", "OK" if restart_ok else "MISMATCH")


if __name__ == "__main__":
    main()
