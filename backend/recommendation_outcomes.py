"""Forward outcome tracking for Recommendations -- what actually happened
after each one, whatever its status, so every pattern has a history of
recommendations AND results (2026-09-19: "history of recommendations and
the outcome"). Productionizes scripts/grade_todays_recommendations.py: the
same win rule, the same forward-window math as PatternOutcome
(pattern_outcome_analysis._compute_outcome), but persisted per
recommendation and refreshed on a timer.

Candles come from candles_today first, then candles_historical, merged by
timestamp -- live recommendations resolve from today's candles; after the
end-of-day archive the same rows are found in history.

Rows stay "partial" and are re-evaluated each pass until all checkpoints have
been observed, or the recommendation is a day old (no more live candles are
coming). Rejected recommendations are tracked the same way: comparing
rejected-by-rule outcomes against recommended ones is how a rule's false
alarms and false negatives get measured, per pattern (pattern_summary).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from db.models import (
    CandleHistorical, CandleToday, Recommendation, RecommendationOutcome, SubscribedSymbol,
)
from db.session import session_scope
from pattern_outcome_analysis import _compute_outcome, _truncate_at_data_gap

logger = logging.getLogger(__name__)

CHECKPOINTS = (5, 10, 15, 20, 30)
MAX_WINDOW = max(CHECKPOINTS)
COMPLETE_AFTER = timedelta(days=1)
LOOKBACK = timedelta(days=3)  # older unresolved rows are given up on


def _window(session, symbol_row, timeframe: str, after_ts: datetime) -> List[tuple]:
    """Up to MAX_WINDOW candles strictly after `after_ts`, as
    (ts, close, high, low) tuples, chronological."""
    key = dict(symbol=symbol_row.symbol, exchange_segment=symbol_row.exchange_segment, timeframe=timeframe)
    merged: Dict[datetime, tuple] = {}
    for model in (CandleHistorical, CandleToday):  # today's rows win on a timestamp clash
        rows = (
            session.query(model).filter_by(**key).filter(model.ts > after_ts)
            .order_by(model.ts).limit(MAX_WINDOW).all()
        )
        for c in rows:
            merged[c.ts] = (c.ts, float(c.close_price), float(c.high_price), float(c.low_price))
    return [merged[ts] for ts in sorted(merged)][:MAX_WINDOW]


def first_hit(rec: Recommendation, window: List[tuple]):
    """("target"|"sl"|"none", candles_to_hit) of the recommendation's OWN
    suggested levels against the forward candles' high/low, or (None, None)
    when it carries no suggested SL/target. A candle that reaches both
    counts as "sl" -- the conservative same-candle rule order_backtest.py
    already uses (the true order inside one candle is unknowable)."""
    if rec.suggested_sl_price is None or rec.suggested_target_price is None:
        return None, None
    sl, tg = float(rec.suggested_sl_price), float(rec.suggested_target_price)
    for i, (_ts, _close, high, low) in enumerate(window, start=1):
        if rec.direction == "bull":
            hit_sl, hit_tg = low <= sl, high >= tg
        else:
            hit_sl, hit_tg = high >= sl, low <= tg
        if hit_sl:
            return "sl", i
        if hit_tg:
            return "target", i
    return "none", None


def build_outcome(rec: Recommendation, window: List[tuple], now: datetime) -> dict:
    """The outcome fields for one recommendation given its forward window."""
    window = _truncate_at_data_gap(rec.detected_ts, window)[:MAX_WINDOW]
    entry = float(rec.entry_price)
    out = _compute_outcome(entry, window, CHECKPOINTS)
    checkpoint = rec.qualifying_checkpoint or 5
    pct = win = None
    if len(window) >= checkpoint:
        pct = (window[checkpoint - 1][1] - entry) / entry
        win = pct > 0 if rec.direction == "bull" else pct < 0
    age = now.replace(tzinfo=None) - rec.detected_ts
    complete = len(window) >= max(MAX_WINDOW, checkpoint) or age > COMPLETE_AFTER
    hit, candles_to_hit = first_hit(rec, window)
    return {
        "first_hit": hit, "candles_to_hit": candles_to_hit,
        "status": "complete" if complete else "partial",
        "candles_observed": len(window), "checkpoint": checkpoint, "checkpoint_pct": pct, "win": win,
        **{f"pct_change_{n}": out[f"pct_change_{n}"] for n in CHECKPOINTS},
        "max_favorable_pct": out["max_favorable_pct"], "max_adverse_pct": out["max_adverse_pct"],
    }


def resolve_outcomes(session_factory, now: Optional[datetime] = None) -> int:
    """One pass: (re)evaluates every recommendation from the last few days
    that has no COMPLETE outcome yet. Returns how many rows were written."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.replace(tzinfo=None) - LOOKBACK
    written = 0
    with session_scope(session_factory) as session:
        complete_ids = session.query(RecommendationOutcome.recommendation_id).filter_by(status="complete")
        recs = (
            session.query(Recommendation)
            .filter(Recommendation.detected_ts > cutoff, ~Recommendation.id.in_(complete_ids))
            .all()
        )
        if not recs:
            return 0
        existing = {o.recommendation_id: o for o in session.query(RecommendationOutcome).filter(
            RecommendationOutcome.recommendation_id.in_([r.id for r in recs]))}
        symbols = {s.id: s for s in session.query(SubscribedSymbol).filter(
            SubscribedSymbol.id.in_({r.instrument_id for r in recs}))}
        for rec in recs:
            symbol_row = symbols.get(rec.instrument_id)
            if symbol_row is None:
                continue
            fields = build_outcome(rec, _window(session, symbol_row, rec.timeframe, rec.detected_ts), now)
            row = existing.get(rec.id)
            if row is None:
                session.add(RecommendationOutcome(recommendation_id=rec.id, **fields))
            else:
                for key, value in fields.items():
                    setattr(row, key, value)
                row.resolved_at = now.replace(tzinfo=None)
            written += 1
    return written


def rejected_row(rec: Recommendation) -> bool:
    return rec.status == "rejected"


def pattern_summary(session, since: Optional[datetime] = None, instrument_id: Optional[int] = None) -> List[dict]:
    """Per-pattern history roll-up. `recommended` = anything not rejected;
    win_pct is over GRADED recommended rows (their checkpoint has passed),
    rejected_win_pct likewise over graded rejected rows -- a high
    rejected_win_pct means the rules are throwing away winners (false
    negatives); a low win_pct means recommendations that still fire are
    false alarms."""
    query = session.query(Recommendation, RecommendationOutcome).outerjoin(
        RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.id)
    if since is not None:
        query = query.filter(Recommendation.detected_ts >= since)
    if instrument_id is not None:
        query = query.filter(Recommendation.instrument_id == instrument_id)

    agg: Dict[str, dict] = {}
    for rec, out in query:
        a = agg.setdefault(rec.pattern, {
            "pattern": rec.pattern, "total": 0, "recommended": 0, "rejected": 0, "rule_rejected": 0,
            "graded": 0, "wins": 0, "rejected_graded": 0, "rejected_wins": 0,
            "target_hits": 0, "sl_hits": 0,
        })
        if out is not None and not rejected_row(rec):
            a["target_hits"] += 1 if out.first_hit == "target" else 0
            a["sl_hits"] += 1 if out.first_hit == "sl" else 0
        a["total"] += 1
        rejected = rec.status == "rejected"
        if rejected:
            a["rejected"] += 1
            if (rec.veto_reason or "").startswith("rules failed"):
                a["rule_rejected"] += 1
        else:
            a["recommended"] += 1
        if out is not None and out.win is not None:
            graded_key, wins_key = ("rejected_graded", "rejected_wins") if rejected else ("graded", "wins")
            a[graded_key] += 1
            a[wins_key] += 1 if out.win else 0
    rows = []
    for a in sorted(agg.values(), key=lambda x: x["pattern"]):
        a["win_pct"] = a["wins"] / a["graded"] if a["graded"] else None
        a["rejected_win_pct"] = a["rejected_wins"] / a["rejected_graded"] if a["rejected_graded"] else None
        rows.append(a)
    return rows


def start_outcome_scanner(session_factory, interval_seconds: int = 300) -> None:
    """Self-rescheduling daemon timer -- same shape as feed/gap_fill.py's
    start_gap_scanner: a failed pass is logged and the next one still runs."""
    def _tick():
        try:
            resolve_outcomes(session_factory)
        except Exception:
            logger.exception("recommendation_outcomes: pass failed")
        finally:
            _schedule()

    def _schedule():
        timer = threading.Timer(interval_seconds, _tick)
        timer.daemon = True
        timer.start()

    _schedule()
