"""Paper backtest runner (rulebook 3a, chunk 4).

One master = shared stocks + dates; each run = its own patterns + settings.
Per stock, past 1-min candles go through the same ActivityEngine +
PatternEvaluator as live (1/3/5-min rolled up by the CandleAggregator), so a
backtest hit is the hit live would have made. Hits are found once per stock
for all runs' patterns; each run then trades them with its own settings via
paper.trade_logic (the live rules), exits on 1-min candles.

READ-ONLY on the real DB except the paper_backtest_* tables: engine writes go
to a throwaway in-memory SQLite. Patterns (incl. the user's own copies) are
read from the real DB. Stocks without 1-min candles are skipped and listed.
Runs in its own process (scripts/run_paper_backtest.py), never inside the
live app, so CPU use can't slow live ticks.
"""
from __future__ import annotations

import bisect
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from activity_engine import ActivityEngine
from brokers.models import Candle
from db.models import Base, SubscribedSymbol
from db.models_paper import PaperBacktestMaster, PaperBacktestRun, PaperBacktestTrade
from db.ops import LibCandles, LibCandlesHistorical
from db.ops.LibPatterns import seed_system_patterns
from db.ops.LibSignalStrength import seed_defaults as seed_strength
from db.ops.LibSystemSettings import seed_defaults as seed_settings
from feed.candle_aggregator import CandleAggregator
from paper.live_paper import wilder_atr
from paper.trade_logic import (PaperSettings, close_trade, day_end_ts, first_opposite_swing, ist_date,
                               on_candle, open_trade, stretch_end)
from pattern_engine import PatternEvaluator
from patterns_catalog import PATTERN_SETTING_SEED, SYSTEM_PATTERNS
from signal_strength_defaults import SIGNAL_STRENGTH_BASE, STRENGTH_SETTING_SEED
from timeframes import candle_duration

logger = logging.getLogger(__name__)
IST = timedelta(hours=5, minutes=30)
WARMUP_DAYS = 6
_TRADE_FIELDS = {c.name for c in PaperBacktestTrade.__table__.columns} - {"id", "created_at", "updated_at"}


def _utc(ts):
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _naive(ts):
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def load_1min(factory, symbol, segment, d_from: date, d_to: date) -> List[Candle]:
    start = datetime.combine(d_from - timedelta(days=WARMUP_DAYS), datetime.min.time())
    end = datetime.combine(d_to + timedelta(days=1), datetime.min.time()) - IST
    by_ts = {}
    with factory() as s:
        for ops in (LibCandlesHistorical, LibCandles):
            for r in ops.get_range(s, symbol, segment, "1min", start, end):
                ts = _utc(r.ts)
                by_ts[ts] = Candle(symbol=symbol, timeframe="1min", timestamp=ts, open=float(r.open_price),
                                   high=float(r.high_price), low=float(r.low_price), close=float(r.close_price),
                                   volume=int(r.volume or 0))
    return [by_ts[t] for t in sorted(by_ts)]


def scratch_factory(symbol, segment):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng)
    seed_settings(factory, PATTERN_SETTING_SEED)
    seed_settings(factory, STRENGTH_SETTING_SEED)
    seed_system_patterns(factory, SYSTEM_PATTERNS)
    seed_strength(factory, SIGNAL_STRENGTH_BASE)
    with factory() as s:
        s.add(SubscribedSymbol(symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment=segment,
                               security_id="0", previous_close=0))
        s.commit()
    return factory


def find_hits(real_factory, owner_id, symbol, segment, candles, d_from, keys, atr_needs,
              swings_out: Optional[list] = None, batch_mode: Optional[int] = None, explain: bool = False) -> List[dict]:
    """Every hit of `keys` on days >= d_from. atr_needs: {(period, tf)} --
    each hit carries atr[(period, tf)], tf 'hit' = the hit's own timeframe,
    as the engine held it at the hit moment exactly like live. swings_out, when
    given, collects every confirmed swing {timeframe, kind, price, ts,
    confirm_ts} from d_from on (for the stretch end).

    Patterns are evaluated one day at a time in a batch (same hits as candle
    by candle, 2026-11-10); batch_mode overrides the setting pattern_batch_eval
    (0 = candle by candle, for comparisons). explain: keep the "why it formed"
    notes of hits and raw signals -- off by default, nothing in a backtest
    reads them and they are about half the run time."""
    factory = scratch_factory(symbol, segment)
    engine = ActivityEngine(factory)
    evaluator = PatternEvaluator(real_factory, owner_id=owner_id, only_keys=keys, batch_mode=batch_mode, explain=explain)
    engine.pattern_evaluator = evaluator
    engine.explain_signals = explain
    hits: List[dict] = []
    last_swing: Dict[str, datetime] = {}
    keep = max([60] + [period * 4 for period, _ in atr_needs])
    seen: Dict[str, list] = defaultdict(list)  # timeframe -> [(order, row)] in processing order
    order_of: Dict[tuple, int] = {}  # (timeframe, ts) -> processing order
    state = {"day": None, "n": 0}

    def add_hits(acts) -> None:
        for a in acts:
            if a.get("activity_type") != "pattern" or a["activity"] not in keys:
                continue
            meta = evaluator.meta(a["activity"])
            at = order_of.get((a["timeframe"], _utc(a["ts"])), state["n"])
            atr = {}
            for period, tf in atr_needs:
                frame_tf = a["timeframe"] if tf == "hit" else tf
                rows = [r for n, r in seen[frame_tf] if n <= at][-period * 4:]
                atr[(period, tf)] = wilder_atr(rows, period) if len(rows) > period else None
            hits.append({"key": a["activity"], "timeframe": a["timeframe"], "ts": _naive(a["ts"]),
                         "close": float(a["close_price"]), "match": a.get("intensity"), "atr": atr, "_order": at,
                         "direction": {"bullish": "buy", "bearish": "sell"}.get(meta[1] if meta else None)})

    def end_day() -> None:
        if state["day"] is not None:
            add_hits(engine.end_deferred_patterns(symbol, segment))
            del engine._buffer[:]
        for tf in seen:
            del seen[tf][:-keep]
        order_of.clear()  # only the day being deferred is looked up

    def note_row(sym, seg, c) -> None:
        state["n"] += 1
        last = engine.recent_rows(sym, seg, c.timeframe, 1)
        if last and _utc(last[-1]["ts"]) == _utc(c.timestamp):
            seen[c.timeframe].append((state["n"], last[-1]))
            order_of[(c.timeframe, _utc(c.timestamp))] = state["n"]

    def on_closed(sym, seg, c):
        day = ist_date(c.timestamp)
        if day < d_from:
            engine.warm_up(sym, seg, c)
            note_row(sym, seg, c)
            return
        if day != state["day"]:
            end_day()
            state["day"] = day
            engine.begin_deferred_patterns(sym, seg)
        start = len(engine._buffer)
        engine.on_candle_closed(sym, seg, c)
        note_row(sym, seg, c)
        if swings_out is not None:
            for p in engine.get_swing_points(sym, seg, c.timeframe):
                p_ts = _naive(p.candle.timestamp)
                if p_ts > last_swing.get(c.timeframe, datetime.min):
                    last_swing[c.timeframe] = p_ts
                    swings_out.append({"timeframe": c.timeframe, "kind": p.kind, "price": p.price, "ts": p_ts,
                                       "confirm_ts": _naive(c.timestamp)})
        add_hits(engine._buffer[start:])  # candle by candle (batch off); deferred hits come from end_day
        del engine._buffer[:]  # keep memory flat over long ranges

    agg = CandleAggregator(on_candle_closed=on_closed)
    for c in candles:
        agg.ingest_historical_1min(symbol, segment, c)
    if candles:
        agg.flush_all(as_of=candles[-1].timestamp + timedelta(minutes=1))
    end_day()
    hits.sort(key=lambda h: (h["ts"], h["_order"]))  # processing order, as candle by candle
    for h in hits:
        del h["_order"]
    return hits


def trade_run(hits, candles_1m, settings: PaperSettings, keys, swings: Optional[list] = None) -> List[dict]:
    """Trades one run's hits with its settings. A hit while the same item
    is still open on this stock is recorded as not taken."""
    ts_list = [_naive(c.timestamp) for c in candles_1m]
    m1 = [(ts_list[i], c.high, c.low) for i, c in enumerate(candles_1m)]
    busy_until: Dict[str, datetime] = {}
    out = []
    for h in hits:
        if h["key"] not in keys:
            continue
        busy = h["ts"] < busy_until.get(h["key"], datetime.min)
        t = open_trade(hit_ts=h["ts"], direction=h["direction"] or "", hit_close=h["close"],
                       atr=h["atr"].get((settings.atr_period, settings.atr_timeframe)),
                       settings=settings, already_open=busy)
        t.update(item_type="pattern", item_key=h["key"], timeframe=h["timeframe"],
                 direction=h["direction"] or "none", match_pct=h["match"])
        if t["status"] == "open":
            i = bisect.bisect_left(ts_list, h["ts"] + candle_duration(h["timeframe"]))
            for c in candles_1m[i:]:
                if on_candle(t, ts=ts_list[i], open_=c.open, high=c.high, low=c.low, close=c.close,
                             settings=settings):
                    break
                i += 1
            if t["status"] == "open":  # data ran out
                last = candles_1m[-1]
                close_trade(t, exit_ts=_naive(last.timestamp), exit_price=last.close, reason="data_end",
                            slippage_pct=settings.slippage_pct)
            busy_until[h["key"]] = t["exit_ts"] + timedelta(minutes=1)
            if swings is not None:
                add_stretch(t, swings, m1, settings)
        out.append(t)
    return out


def add_stretch(t: dict, swings: list, m1: list, settings: PaperSettings) -> None:
    """Stretch end for one taken trade (same rule as live)."""
    tf = candle_duration(t["timeframe"])
    de = day_end_ts(t["hit_ts"], settings.day_end)
    s = first_opposite_swing(t["direction"], swings, t["hit_ts"], t["timeframe"], confirmed_before=de)
    end, reason = (s["ts"] + tf, "swing") if s else (de, "day_end")
    t.update(stretch_end(t["direction"], t["entry_price"], m1, t["hit_ts"] + tf, end), stretch_reason=reason)


def summarize(trades: List[dict], d_from: date, d_to: date) -> dict:
    taken = sorted((t for t in trades if t["status"] == "closed"), key=lambda t: t["exit_ts"])
    mid = d_from + (d_to - d_from) / 2

    def stats(rows):
        if not rows:
            return {"trades": 0, "win_pct": None, "avg_net_pct": None, "net_pnl": 0.0}
        wins = sum(1 for t in rows if t["net_pnl"] > 0)
        return {"trades": len(rows), "win_pct": round(wins / len(rows) * 100, 2),
                "avg_net_pct": round(sum(t["net_pct"] for t in rows) / len(rows), 4),
                "net_pnl": round(sum(t["net_pnl"] for t in rows), 2)}

    equity = peak = drawdown = 0.0
    streak = worst_streak = 0
    for t in taken:
        equity += t["net_pnl"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        streak = streak + 1 if t["net_pnl"] <= 0 else 0
        worst_streak = max(worst_streak, streak)
    by_stock = defaultdict(float)
    for t in taken:
        by_stock[t["symbol"]] += t["net_pnl"]
    out = stats(taken)
    out.update(
        not_taken=sum(1 for t in trades if t["status"] == "not_taken"),
        max_drawdown=round(drawdown, 2), worst_losing_streak=worst_streak,
        stocks_traded=len(by_stock), stocks_positive=sum(1 for v in by_stock.values() if v > 0),
        first_half=stats([t for t in taken if ist_date(t["hit_ts"]) < mid]),
        second_half=stats([t for t in taken if ist_date(t["hit_ts"]) >= mid]),
        exits={r: sum(1 for t in taken if t["exit_reason"] == r) for r in ("stop", "target", "day_end", "data_end")},
        # how far the move went vs how much of it our exit kept (both price %, before costs)
        avg_stretch_pct=_avg([t.get("stretch_pct") for t in taken]),
        avg_exit_move_pct=_avg([(1 if t["direction"] == "buy" else -1) * (t["exit_price"] - t["entry_price"])
                                / t["entry_price"] * 100 for t in taken]),
    )
    return out


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def run_master(real_factory, master_id: int) -> None:
    with real_factory() as s:
        m = s.get(PaperBacktestMaster, master_id)
        runs = s.query(PaperBacktestRun).filter_by(master_id=master_id).order_by(PaperBacktestRun.id).all()
        s.expunge_all()
    instrument_ids = json.loads(m.symbols)
    d_from, d_to = m.date_from.date(), m.date_to.date()
    plan: List[Tuple[PaperBacktestRun, PaperSettings, set]] = [
        (r, PaperSettings.from_row(r), {i["item_key"] for i in json.loads(r.items)}) for r in runs]
    all_keys = set().union(*(k for _, _, k in plan))
    atr_needs = {(st.atr_period, st.atr_timeframe) for _, st, _ in plan}
    _set_status(real_factory, master_id, status="running", progress_pct=0.0, error=None)
    with real_factory() as s:
        syms = {r.id: (r.symbol, r.exchange_segment) for r in
                s.query(SubscribedSymbol).filter(SubscribedSymbol.id.in_(instrument_ids))}
    per_run: Dict[int, List[dict]] = defaultdict(list)
    skipped = []
    try:
        for n, iid in enumerate(instrument_ids, 1):
            if iid not in syms:
                skipped.append(f"#{iid}: not found")
                continue
            symbol, segment = syms[iid]
            candles = load_1min(real_factory, symbol, segment, d_from, d_to)
            if not any(ist_date(c.timestamp) >= d_from for c in candles):
                skipped.append(f"{symbol}: no 1-min candles in range")
            else:
                swings: list = []
                hits = find_hits(real_factory, m.owner_id, symbol, segment, candles, d_from, all_keys, atr_needs,
                                 swings_out=swings)
                in_range = [c for c in candles if ist_date(c.timestamp) >= d_from]
                with real_factory() as s:
                    for run, settings, keys in plan:
                        trades = trade_run(hits, in_range, settings, keys, swings=swings)
                        for t in trades:
                            t.update(owner_id=m.owner_id, instrument_id=iid, symbol=symbol)
                            row = PaperBacktestTrade(run_id=run.id)
                            for k, v in t.items():
                                if k in _TRADE_FIELDS:
                                    setattr(row, k, v)
                            s.add(row)
                        per_run[run.id].extend(trades)
                    s.commit()
            _set_status(real_factory, master_id, progress_pct=round(n / len(instrument_ids) * 100, 1))
        with real_factory() as s:
            for run, _, _ in plan:
                summary = summarize(per_run[run.id], d_from, d_to)
                summary["skipped_stocks"] = skipped
                s.get(PaperBacktestRun, run.id).summary = json.dumps(summary, default=str)
            s.commit()
        _set_status(real_factory, master_id, status="done", progress_pct=100.0,
                    finished_at=datetime.now(timezone.utc).replace(tzinfo=None))
    except Exception as e:
        logger.exception("paper backtest %s failed", master_id)
        _set_status(real_factory, master_id, status="failed", error=str(e)[:2000])
        raise


def _set_status(factory, master_id, **fields) -> None:
    with factory() as s:
        m = s.get(PaperBacktestMaster, master_id)
        for k, v in fields.items():
            setattr(m, k, v)
        s.commit()
