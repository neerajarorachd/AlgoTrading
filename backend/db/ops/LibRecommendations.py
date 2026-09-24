"""Recommendation reads/writes/queue-selection — see db/models.py's
Recommendation for the full column/status-lifecycle rationale. Pure
persistence layer: never imports recommendation_engine.py (which owns the
actual generation/regeneration business logic and calls INTO this module,
not the reverse — same one-way layering as pattern_outcome_analysis.py ->
LibPatternOutcomes.py).

Note this table is fed by the ANALYSIS layer (LibPatternOutcomes — a
neutral "what happened after this pattern fired" statistic), not the
BACKTESTING layer (LibBacktestRuns/run_backtest.py, which simulates actual
position-sized trades). The two are separate subsystems; a Recommendation
row's `source` field names which ANALYSIS system produced it."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from db.models import Recommendation
from db.session import session_scope


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_bulk(session_factory, rows: List[dict]) -> List[Optional[int]]:
    """Optimistic bulk insert with a per-row IntegrityError fallback — same
    shape as LibActivities.persist_bulk/LibPatternOutcomes.persist_bulk. A
    duplicate on the full unique key (instrument_id, timeframe, pattern,
    direction, detected_ts, regeneration_count, recommendation_system_id) —
    e.g. a replay re-processing the same live candle — is silently skipped
    (None at that position in the returned list), never a duplicate row."""
    if not rows:
        return []
    objs = [Recommendation(**row) for row in rows]
    try:
        with session_scope(session_factory) as session:
            session.add_all(objs)
            session.flush()
            return [o.id for o in objs]
    except IntegrityError:
        return [create_one(session_factory, row) for row in rows]


def create_one(session_factory, row: dict) -> Optional[int]:
    try:
        with session_scope(session_factory) as session:
            obj = Recommendation(**row)
            session.add(obj)
            session.flush()
            return obj.id
    except IntegrityError:
        return None


def get_pending(
    session, instrument_id: Optional[int] = None, timeframe: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[Recommendation]:
    """status="queued" AND not yet expired — the raw queue contents, no
    ranking (that's pick_best's job). Used by a future review UI/API and
    by pick_best itself."""
    now = now or _utcnow()
    query = session.query(Recommendation).filter(
        Recommendation.status == "queued", Recommendation.expires_at > now,
    )
    if instrument_id is not None:
        query = query.filter(Recommendation.instrument_id == instrument_id)
    if timeframe is not None:
        query = query.filter(Recommendation.timeframe == timeframe)
    return query.all()


def list_recent(
    session, instrument_id: Optional[int] = None, timeframe: Optional[str] = None,
    since: Optional[datetime] = None, limit: int = 200, pattern: Optional[str] = None,
) -> List[Recommendation]:
    """Every row regardless of status/expiry, most-recently-detected first —
    a historical record view (e.g. "what fired today"), unlike get_pending's
    still-actionable-only filter. A recommendation's own actionable window
    is deliberately short (often ~1 candle), so most of a day's signals are
    already "expired" by the time anyone looks — that's correct queue
    behavior, not a sign nothing happened; this is the view for seeing what
    did."""
    query = session.query(Recommendation)
    if instrument_id is not None:
        query = query.filter(Recommendation.instrument_id == instrument_id)
    if timeframe is not None:
        query = query.filter(Recommendation.timeframe == timeframe)
    if since is not None:
        query = query.filter(Recommendation.detected_ts >= since)
    if pattern is not None:
        query = query.filter(Recommendation.pattern == pattern)
    return query.order_by(Recommendation.detected_ts.desc()).limit(limit).all()


def pick_best(
    session, available_slots: int, instrument_id: Optional[int] = None,
    timeframe: Optional[str] = None, now: Optional[datetime] = None,
) -> List[Recommendation]:
    """"Best option wins" — ranks the current queue by wilson_score (which
    accounts for both win% AND sample size together, per the explicit
    instruction: "30 historical wins with 75% will win over 2 historical
    wins with 90% win rate"), breaking ties on confirmations_passed. Since
    the 2026-09-16 guiding-scenario redesign every new row has
    confirmations_passed=0 (no live confirmation/veto step exists anymore —
    see Recommendation's own docstring), so this tie-break is effectively a
    no-op for new rows; kept rather than removed since it's still correct
    for any pre-redesign row still in the queue, and returns up to
    `available_slots` candidates.

    Dedupes by SIGNAL first (instrument_id, timeframe, pattern, direction,
    detected_ts) — one row per pattern occurrence per generation round, but
    a regeneration chain ("go for analysis again" on expiry) can still
    leave more than one row sharing the same signal key over time
    (different regeneration_count); only that signal's single best-scoring
    row is eligible to consume one of the caller's slots, never more than
    one. Does NOT mutate any row's status — a caller that actually acts on
    a result must call mark_selected itself, keeping "what would be
    picked" (safe to call repeatedly, e.g. for a preview API) separate
    from "this got picked" (a one-time state transition)."""
    candidates = get_pending(session, instrument_id, timeframe, now)
    best_by_signal: Dict[Tuple, Recommendation] = {}
    for row in candidates:
        key = (row.instrument_id, row.timeframe, row.pattern, row.direction, row.detected_ts)
        current_best = best_by_signal.get(key)
        if current_best is None or float(row.wilson_score) > float(current_best.wilson_score):
            best_by_signal[key] = row
    ranked = sorted(
        best_by_signal.values(),
        key=lambda r: (float(r.wilson_score), r.confirmations_passed), reverse=True,
    )
    return ranked[:max(available_slots, 0)]


def mark_selected(session_factory, recommendation_id: int) -> bool:
    """Transitions one row to "selected" and marks every SIBLING row for
    the same underlying signal (same instrument_id/timeframe/pattern/
    direction/detected_ts, still "queued") as "superseded" — picking one
    dimension's recommendation for this signal means the others can never
    also be acted on, they represented the same trade opportunity. Returns
    False if the row doesn't exist or isn't currently "queued" (already
    selected/expired/superseded/rejected — never re-selectable)."""
    with session_scope(session_factory) as session:
        row = session.query(Recommendation).filter_by(id=recommendation_id).one_or_none()
        if row is None or row.status != "queued":
            return False
        row.status = "selected"
        row.selected_at = _utcnow()
        session.query(Recommendation).filter(
            Recommendation.instrument_id == row.instrument_id,
            Recommendation.timeframe == row.timeframe,
            Recommendation.pattern == row.pattern,
            Recommendation.direction == row.direction,
            Recommendation.detected_ts == row.detected_ts,
            Recommendation.status == "queued",
            Recommendation.id != row.id,
        ).update({"status": "superseded"}, synchronize_session=False)
        return True


def sweep_expired(session_factory, now: Optional[datetime] = None) -> List[dict]:
    """Marks every "queued" row past its own expires_at as "expired".
    Returns each swept row as a plain dict (read while the session is
    still open, avoiding DetachedInstanceError after session_scope exits)
    — recommendation_engine.sweep_and_regenerate is the caller that
    decides, per row, whether regeneration_count still has room to
    "go for analysis again" (that decision needs the current SystemSetting
    values, which this pure db/ops module deliberately doesn't read)."""
    now = now or _utcnow()
    with session_scope(session_factory) as session:
        rows = session.query(Recommendation).filter(
            Recommendation.status == "queued", Recommendation.expires_at <= now,
        ).all()
        swept = []
        for row in rows:
            row.status = "expired"
            swept.append({
                "id": row.id, "source": row.source, "recommendation_system_id": row.recommendation_system_id,
                "instrument_id": row.instrument_id, "timeframe": row.timeframe,
                "pattern": row.pattern, "direction": row.direction, "entry_price": float(row.entry_price),
                "detected_ts": row.detected_ts, "regeneration_count": row.regeneration_count,
                "signal_intensity": float(row.signal_intensity) if row.signal_intensity is not None else None,
                "signal_rsi_state": row.signal_rsi_state, "signal_rsi_trend": row.signal_rsi_trend,
                "signal_macd_state": row.signal_macd_state, "signal_macd_trend": row.signal_macd_trend,
                "signal_stoch_state": row.signal_stoch_state, "signal_stoch_trend": row.signal_stoch_trend,
                "signal_rsi": float(row.signal_rsi) if row.signal_rsi is not None else None,
                "signal_macd_line": float(row.signal_macd_line) if row.signal_macd_line is not None else None,
                "signal_macd_signal": float(row.signal_macd_signal) if row.signal_macd_signal is not None else None,
                "signal_stoch_k": float(row.signal_stoch_k) if row.signal_stoch_k is not None else None,
                "guiding_scenario_id": row.guiding_scenario_id, "window_kind": row.window_kind,
            })
        return swept
