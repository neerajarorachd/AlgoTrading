"""Temporary, deliberately simple prediction-tracking layer for graph
formations and indicator crossovers fired by activity_engine.py.

This is NOT the real Strategy/Entry-monitor/Exit-monitor system described
in the project's own strategy_system_architecture notes — it's a throwaway-
friendly stand-in built specifically to test whether graph formations and
crossovers are actually predictive, per the explicit instruction: "this is
a temporary arrangement so do not stitch predictions too deep into the
formations." Concretely, this reads activity_engine.ActivityEngine's own
public, read-only accessors (on_candle_closed's return value, get_atr(),
get_swing_points()) rather than having any detection logic threaded through
it — activity_engine.py has zero awareness this module exists.

Each qualifying pattern is treated as its own standalone "strategy" for
now, not combined with anything else, so the system keeps producing a
steady stream of predictions to evaluate (2026-09-14 instruction: "each
complicated formation we can treat as a standalone strategy so that we
should keep on getting predictions").

Two moving parts:
  - on_activities(): opens a prediction the instant a tracked pattern
    fires.
  - check_pending(): call once per closed candle (regardless of whether
    anything fired) to resolve every open prediction for that instrument/
    timeframe against the candle's own high/low.

Both are pure in-memory operations — no DB round trip on the per-candle
hot path at all. This went through two rounds of fixing real, measured
bottlenecks (2026-09-14):
  1. check_pending originally ran one SELECT per closed candle regardless
     of whether anything was pending — a 6-stock x 3-timeframe replay took
     45+ minutes. Fixed by tracking pending predictions in self._pending
     (keyed by (instrument_id, timeframe)), seeded once at construction
     from whatever's still open in the DB (_load_pending — so a live
     process restarting mid-day resumes tracking instead of orphaning
     them) and kept in sync from there. That alone only got to ~23
     minutes for the same 6 stocks.
  2. The remaining cost was one INSERT per opened prediction and one
     UPDATE per resolved one — individually cheap, but 1500+ predictions/
     day across 6 stocks meant ~3000 round trips. Fixed the same way
     ActivityEngine's own buffer/flush already solves this for candle
     activities: opens and resolves are buffered in memory
     (self._unpersisted / self._resolved_buffer) and written in bulk via
     flush(), called automatically roughly once a minute of wall-clock
     time (FLUSH_INTERVAL_SECONDS) rather than needing a caller to
     remember to call it on a timer — self-contained so both the replay
     script and any future live wiring get the same behavior for free.
     Callers should still call flush() explicitly once at shutdown/end of
     a run to catch whatever's buffered since the last automatic flush.
  Idempotency (never re-opening a prediction a prior run already
  recorded) no longer relies on a per-open INSERT hitting the DB's unique
  constraint — self._known_keys is seeded once at construction from every
  existing (instrument_id, timeframe, pattern, detected_ts) already in the
  table (a lightweight columns-only query), so a duplicate open is caught
  in memory before it's ever buffered for insert. The unique constraint
  still exists as a backstop for a genuine concurrent-writer race; flush()
  falls back to inserting one row at a time on IntegrityError so one
  conflict doesn't drop the rest of a batch, matching activity_engine.py's
  own _persist_bulk/_persist_one pattern.

  A prediction still open when the DB is queried directly (e.g. for
  analysis) may show candles_checked at whatever it was on its last flush,
  not live-updated every candle — matches this module's "start simple"
  stance elsewhere; the final resolved value is always accurate once it
  actually resolves and gets flushed.

Stop-loss/target sourcing:
  - double/triple top/bottom reuse activity_engine's own neckline-based
    measured-move formulas (activity_engine.FORMATION_LEVEL_FUNCS), read
    via engine.get_swing_points() — the same points list the detector
    itself used, not re-derived here.
  - every crossover pattern (RSI/MACD/Stochastic/MA, plus the structure-
    shift/break-of-structure patterns, which have no natural measured-move
    target of their own) uses an ATR-based stop with a fixed risk:reward
    target — the industry-standard starting point for momentum/crossover
    entries (researched 2026-09-14: a 1.5-2x ATR stop with a fixed R:R
    target, e.g. TradersPost's ATR trading guide and similar sources
    converge on this rather than an independently-derived second target
    level). Both numbers are calibratable via the same EngineSetting
    mechanism activity_engine.py's own thresholds use.

Same-candle target/stop ambiguity (a single candle's high/low range
touches both) is a well-known backtesting problem — OHLC data alone can't
say which was hit first. Resolved here by dropping to the instrument's own
1-min candles covering that exact window and checking them in actual
sequence (1-min data exists regardless of which timeframe the prediction
came from); only if even a single 1-min candle is itself ambiguous does
this fall back to the conservative industry convention of assuming the
stop was hit first, so outcomes never overstate success. This one still
queries the DB directly (CandleToday) — it's a genuinely different lookup,
not a re-check of pending state, and only runs on the rare same-candle-
ambiguity case, not per candle.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from activity_engine import ActivityEngine, FORMATION_LEVEL_FUNCS
from brokers.models import Candle
from db.models import CandleToday, EngineSetting, PatternPrediction, SubscribedSymbol
from db.session import session_scope

logger = logging.getLogger(__name__)

# Middle of the "20-30 candles" outcome-scan window used throughout this
# project's backtesting-calibration notes — after this many candles with
# neither target nor stop hit, a pending prediction is closed out as
# "sideways" rather than left open forever.
OUTCOME_SCAN_CANDLES = 25

# How often flush() is called automatically (wall-clock, not simulated
# market time — a replay run processes a whole day in seconds, so this
# still fires every ~60s of real time spent replaying, roughly bounding
# how much sits unpersisted in memory rather than tracking candle count).
FLUSH_INTERVAL_SECONDS = 60

_TIMEFRAME_MINUTES = {"1min": 1, "3min": 3, "5min": 5}

# Calibratable via the same EngineSetting table activity_engine.py's own
# thresholds use (key namespace shared, no schema change needed).
PREDICTION_SETTING_DEFAULTS: Dict[str, float] = {
    "crossover_atr_stop_multiplier": 1.5,
    "crossover_risk_reward_ratio": 2.0,
}

# Which fired activities open a prediction, and which direction they imply.
# A flat lookup table here (not inside activity_engine.py) so adding or
# removing a tracked pattern never touches detection code.
_BULLISH_PATTERNS = {
    "rsi_cross_above_60", "macd_bullish_cross", "ma_golden_cross",
    "stoch_bullish_cross", "double_bottom", "triple_bottom",
    "bullish_structure_shift", "bullish_break_of_structure",
}
_BEARISH_PATTERNS = {
    "rsi_cross_below_40", "macd_bearish_cross", "ma_death_cross",
    "stoch_bearish_cross", "double_top", "triple_top",
    "bearish_structure_shift", "bearish_break_of_structure",
}
# These have their own neckline-derived measured-move levels (see
# activity_engine.FORMATION_LEVEL_FUNCS) instead of the generic ATR-based
# formula every other tracked pattern uses.
_GRAPH_FORMATIONS = set(FORMATION_LEVEL_FUNCS.keys())


def load_prediction_settings(session_factory) -> Dict[str, float]:
    with session_scope(session_factory) as session:
        rows = (
            session.query(EngineSetting)
            .filter(EngineSetting.key.in_(list(PREDICTION_SETTING_DEFAULTS.keys())))
            .all()
        )
        return {row.key: float(row.value) for row in rows}


def crossover_stop_loss(entry: float, atr: float, direction: str, atr_multiplier: float) -> float:
    risk = atr * atr_multiplier
    return entry - risk if direction == "bull" else entry + risk


def crossover_target(entry: float, atr: float, direction: str, atr_multiplier: float, risk_reward_ratio: float) -> float:
    reward = atr * atr_multiplier * risk_reward_ratio
    return entry + reward if direction == "bull" else entry - reward


def _as_utc(dt: datetime) -> datetime:
    """DB round trips strip tzinfo (SQL Server/SQLite DateTime columns
    return naive values); freshly-opened predictions carry whatever
    tzinfo the source Candle used (aware UTC, per this project's own "UTC
    throughout" convention). Normalizing both to aware UTC here means
    in-memory comparisons never mix naive and aware datetimes regardless
    of which path a given _Pending came from."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@dataclass
class _Pending:
    """In-memory shadow of one prediction — open or already resolved but
    not yet flushed. id is None until the first flush() actually inserts
    it; outcome/outcome_ts are set the moment check_pending resolves it,
    independent of whether it's been flushed yet."""
    id: Optional[int]
    instrument_id: int
    timeframe: str
    pattern: str
    direction: str
    detected_ts: datetime
    entry_price: float
    neckline: Optional[float]
    stop_loss: float
    target: float
    candles_checked: int = 0
    outcome: Optional[str] = None
    outcome_ts: Optional[datetime] = None


class PredictionTracker:
    def __init__(self, session_factory, engine: ActivityEngine):
        self.session_factory = session_factory
        self.engine = engine
        settings = load_prediction_settings(session_factory)
        self.atr_multiplier = settings.get(
            "crossover_atr_stop_multiplier", PREDICTION_SETTING_DEFAULTS["crossover_atr_stop_multiplier"]
        )
        self.risk_reward_ratio = settings.get(
            "crossover_risk_reward_ratio", PREDICTION_SETTING_DEFAULTS["crossover_risk_reward_ratio"]
        )
        # Deliberately its own small instrument-id cache rather than reaching
        # into ActivityEngine's private one — keeps this module able to be
        # deleted/replaced without touching activity_engine.py at all.
        self._instrument_ids: Dict[Tuple[str, str], int] = {}
        # pending (unresolved) predictions, in memory — keyed by
        # (instrument_id, timeframe), the same key shape check_pending
        # looks up by.
        self._pending: Dict[Tuple[int, str], List[_Pending]] = defaultdict(list)
        # every (instrument_id, timeframe, pattern, detected_ts) already
        # known — resolved or not — so a duplicate open (a replay re-run)
        # is caught in memory, not by a per-open DB round trip.
        self._known_keys: set = set()
        # buffered writes, flushed in bulk (see module docstring)
        self._unpersisted: List[_Pending] = []
        self._resolved_buffer: List[_Pending] = []
        self._last_flush_at = time.monotonic()
        self._load_existing()

    def _load_existing(self) -> None:
        """One-time queries at construction: seed self._pending with
        whatever's still open (resumes a live process's tracking after a
        restart) and self._known_keys with every prediction ever recorded
        for idempotency. Not part of the per-candle hot path."""
        with session_scope(self.session_factory) as session:
            key_rows = session.query(
                PatternPrediction.instrument_id, PatternPrediction.timeframe,
                PatternPrediction.pattern, PatternPrediction.detected_ts,
            ).all()
            for instrument_id, timeframe, pattern, detected_ts in key_rows:
                self._known_keys.add((instrument_id, timeframe, pattern, _as_utc(detected_ts)))

            pending_rows = session.query(PatternPrediction).filter_by(outcome=None).all()
            for row in pending_rows:
                key = (row.instrument_id, row.timeframe)
                self._pending[key].append(_Pending(
                    id=row.id, instrument_id=row.instrument_id, timeframe=row.timeframe,
                    pattern=row.pattern, direction=row.direction, detected_ts=_as_utc(row.detected_ts),
                    entry_price=float(row.entry_price), neckline=float(row.neckline) if row.neckline is not None else None,
                    stop_loss=float(row.stop_loss), target=float(row.target),
                    candles_checked=row.candles_checked,
                ))

    def on_activities(self, symbol: str, exchange_segment: str, activities: List[dict]) -> int:
        """Call with exactly the list ActivityEngine.on_candle_closed just
        returned. Opens one prediction per tracked pattern found (buffered
        in memory, not yet persisted). Returns how many were opened."""
        opened = 0
        for activity in activities:
            pattern = activity["activity"]
            if pattern in _BULLISH_PATTERNS:
                direction = "bull"
            elif pattern in _BEARISH_PATTERNS:
                direction = "bear"
            else:
                continue  # not a tracked pattern (candle_pattern, price_action, swing_high/low, ...)

            timeframe = activity["timeframe"]
            entry = float(activity["close_price"])

            if pattern in _GRAPH_FORMATIONS:
                points = self.engine.get_swing_points(symbol, exchange_segment, timeframe)
                neckline_fn, stop_fn, target_fn = FORMATION_LEVEL_FUNCS[pattern]
                # double top/bottom need the tail 3 points, triple top/bottom
                # need 5 — defensive: the activity shouldn't have fired
                # without enough points already in hand, but skip rather
                # than guess if get_swing_points somehow comes back short
                needed = 5 if pattern.startswith("triple_") else 3
                if len(points) < needed:
                    continue
                neckline, stop_loss, target = neckline_fn(points), stop_fn(points), target_fn(points)
            else:
                atr = self.engine.get_atr(symbol, exchange_segment, timeframe)
                if atr is None:
                    continue  # not enough data yet to size a stop — skip rather than guess
                neckline = None
                stop_loss = crossover_stop_loss(entry, atr, direction, self.atr_multiplier)
                target = crossover_target(entry, atr, direction, self.atr_multiplier, self.risk_reward_ratio)

            if self._open_prediction(
                activity["instrument_id"], timeframe, pattern, direction,
                activity["ts"], entry, neckline, stop_loss, target,
            ):
                opened += 1
        self._maybe_flush()
        return opened

    def check_pending(self, symbol: str, exchange_segment: str, candle: Candle) -> int:
        """Call once per closed candle, regardless of whether anything
        fired on it. Resolves every pending prediction for this instrument/
        timeframe against the candle's own high/low — purely from the
        in-memory self._pending, no DB query. Returns how many predictions
        were resolved (target/stop hit, or timed out sideways)."""
        instrument_id = self._lookup_instrument_id(symbol, exchange_segment)
        if instrument_id is None:
            return 0
        key = (instrument_id, candle.timeframe)
        pending = self._pending.get(key)
        if not pending:
            return 0

        candle_ts = _as_utc(candle.timestamp)
        resolved = 0
        still_pending = []
        for rec in pending:
            if rec.detected_ts >= candle_ts:
                still_pending.append(rec)
                continue
            outcome = self._evaluate(rec, symbol, exchange_segment, candle)
            if outcome is None:
                still_pending.append(rec)
                continue
            rec.outcome = outcome
            rec.outcome_ts = candle.timestamp
            if rec.id is not None:
                # already flushed once as "pending" — needs an UPDATE
                self._resolved_buffer.append(rec)
            # else: still sitting in self._unpersisted, which will insert
            # it with this final state directly — no separate UPDATE needed
            resolved += 1
        self._pending[key] = still_pending
        self._maybe_flush()
        return resolved

    def _evaluate(self, rec: _Pending, symbol: str, exchange_segment: str, candle: Candle) -> Optional[str]:
        """Mutates rec.candles_checked in place; returns the outcome string
        once resolved, or None while still pending."""
        high, low = float(candle.high), float(candle.low)
        if rec.direction == "bull":
            hit_target, hit_stop = high >= rec.target, low <= rec.stop_loss
        else:
            hit_target, hit_stop = low <= rec.target, high >= rec.stop_loss

        rec.candles_checked += 1

        if hit_target and hit_stop:
            return self._resolve_same_candle_ambiguity(rec, symbol, exchange_segment, candle)
        if hit_target:
            return "target_hit"
        if hit_stop:
            return "stop_hit"
        if rec.candles_checked >= OUTCOME_SCAN_CANDLES:
            return "sideways"
        return None

    def _resolve_same_candle_ambiguity(
        self, rec: _Pending, symbol: str, exchange_segment: str, candle: Candle,
    ) -> str:
        if rec.timeframe == "1min":
            logger.warning(
                "Prediction %s: same 1-min candle hit both target and stop — no finer "
                "data available, assuming stop hit first (conservative default)", rec.id,
            )
            return "stop_hit"

        minutes = _TIMEFRAME_MINUTES.get(rec.timeframe, 1)
        window_end = candle.timestamp
        window_start = window_end - timedelta(minutes=minutes - 1)

        with session_scope(self.session_factory) as session:
            one_min_candles = (
                session.query(CandleToday)
                .filter(
                    CandleToday.symbol == symbol,
                    CandleToday.exchange_segment == exchange_segment,
                    CandleToday.timeframe == "1min",
                    CandleToday.ts >= window_start,
                    CandleToday.ts <= window_end,
                )
                .order_by(CandleToday.ts)
                .all()
            )
            for row in one_min_candles:
                high, low = float(row.high_price), float(row.low_price)
                if rec.direction == "bull":
                    hit_target, hit_stop = high >= rec.target, low <= rec.stop_loss
                else:
                    hit_target, hit_stop = low <= rec.target, high >= rec.stop_loss
                if hit_target and hit_stop:
                    logger.warning(
                        "Prediction %s: even the 1-min candle at %s hit both target and "
                        "stop — assuming stop hit first (conservative default)", rec.id, row.ts,
                    )
                    return "stop_hit"
                if hit_target:
                    return "target_hit"
                if hit_stop:
                    return "stop_hit"

        logger.warning(
            "Prediction %s: coarser candle hit both target and stop but no 1-min candles "
            "were found for the window — assuming stop hit first (conservative default)", rec.id,
        )
        return "stop_hit"

    def _open_prediction(
        self, instrument_id: int, timeframe: str, pattern: str, direction: str,
        detected_ts: datetime, entry: float, neckline: Optional[float], stop_loss: float, target: float,
    ) -> bool:
        """Idempotent on (instrument_id, timeframe, pattern, detected_ts) —
        re-processing the same candle (a replay re-run) must never open a
        duplicate prediction for a pattern that already fired on it.
        Buffers the new prediction in memory; returns whether it was
        actually opened (False if already known)."""
        key = (instrument_id, timeframe, pattern, _as_utc(detected_ts))
        if key in self._known_keys:
            return False
        self._known_keys.add(key)

        rec = _Pending(
            id=None, instrument_id=instrument_id, timeframe=timeframe, pattern=pattern,
            direction=direction, detected_ts=_as_utc(detected_ts), entry_price=entry,
            neckline=neckline, stop_loss=stop_loss, target=target,
        )
        self._pending[(instrument_id, timeframe)].append(rec)
        self._unpersisted.append(rec)
        return True

    def flush(self) -> Tuple[int, int]:
        """Writes every buffered prediction open/resolve to the DB in as
        few round trips as possible, then clears those buffers. Returns
        (inserted, updated) counts. Called automatically roughly once a
        minute (see FLUSH_INTERVAL_SECONDS) — callers should still call
        this explicitly once at shutdown/end of a run to flush whatever's
        left since the last automatic call."""
        inserted = self._flush_inserts()
        updated = self._flush_updates()
        self._last_flush_at = time.monotonic()
        return inserted, updated

    def _maybe_flush(self) -> None:
        if time.monotonic() - self._last_flush_at >= FLUSH_INTERVAL_SECONDS:
            self.flush()

    def _flush_inserts(self) -> int:
        if not self._unpersisted:
            return 0
        recs, self._unpersisted = self._unpersisted, []
        try:
            with session_scope(self.session_factory) as session:
                rows = [self._to_orm_row(rec) for rec in recs]
                session.add_all(rows)
                session.flush()  # assigns .id to every row in one round trip
                for rec, row in zip(recs, rows):
                    rec.id = row.id
        except IntegrityError:
            # a concurrent writer already recorded one of these — fall back
            # to the slower one-row-at-a-time path so one conflict doesn't
            # drop the rest of the batch, matching activity_engine.py's own
            # _persist_bulk/_persist_one fallback
            for rec in recs:
                self._insert_one(rec)
        return len(recs)

    def _insert_one(self, rec: _Pending) -> None:
        try:
            with session_scope(self.session_factory) as session:
                row = self._to_orm_row(rec)
                session.add(row)
                session.flush()
                rec.id = row.id
        except IntegrityError:
            pass  # already recorded — drop this one, keep the rest working

    def _flush_updates(self) -> int:
        if not self._resolved_buffer:
            return 0
        recs, self._resolved_buffer = self._resolved_buffer, []
        with session_scope(self.session_factory) as session:
            session.bulk_update_mappings(PatternPrediction, [
                {
                    "id": rec.id, "outcome": rec.outcome,
                    "outcome_ts": rec.outcome_ts, "candles_checked": rec.candles_checked,
                }
                for rec in recs
            ])
        return len(recs)

    @staticmethod
    def _to_orm_row(rec: _Pending) -> PatternPrediction:
        return PatternPrediction(
            instrument_id=rec.instrument_id, timeframe=rec.timeframe, pattern=rec.pattern,
            direction=rec.direction, detected_ts=rec.detected_ts, entry_price=rec.entry_price,
            neckline=rec.neckline, stop_loss=rec.stop_loss, target=rec.target,
            outcome=rec.outcome, outcome_ts=rec.outcome_ts, candles_checked=rec.candles_checked,
        )

    def _lookup_instrument_id(self, symbol: str, exchange_segment: str) -> Optional[int]:
        cache_key = (symbol, exchange_segment)
        if cache_key in self._instrument_ids:
            return self._instrument_ids[cache_key]
        with session_scope(self.session_factory) as session:
            row = (
                session.query(SubscribedSymbol)
                .filter_by(symbol=symbol, exchange_segment=exchange_segment)
                .one_or_none()
            )
            if row is None:
                return None
            instrument_id = row.id
        self._instrument_ids[cache_key] = instrument_id
        return instrument_id
