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
  - on_activities(): opens a PatternPrediction row the instant a tracked
    pattern fires (immediately, not via buffer/flush — these fire only a
    handful of times a day per instrument, so the DB-round-trip concern
    that motivated ActivityEngine's own buffer/flush doesn't apply here).
  - check_pending(): call once per closed candle (regardless of whether
    anything fired) to resolve every open prediction for that instrument/
    timeframe against the candle's own high/low.

Stop-loss/target sourcing:
  - double/triple top/bottom reuse activity_engine's own neckline-based
    measured-move formulas (activity_engine.FORMATION_LEVEL_FUNCS), read
    via engine.get_swing_points() — the same points list the detector
    itself used, not re-derived here.
  - every crossover pattern (RSI/MACD/Stochastic/MA, plus the structure-
    shift/break-of-structure patterns, which have no natural measured-move
    target of their own) uses an ATR-based stop with a fixed risk:reward
    target — the
    industry-standard starting point for momentum/crossover entries
    (researched 2026-09-14: a 1.5-2x ATR stop with a fixed R:R target,
    e.g. TradersPost's ATR trading guide and similar sources converge on
    this rather than an independently-derived second target level). Both
    numbers are calibratable via the same EngineSetting mechanism
    activity_engine.py's own thresholds use.

Same-candle target/stop ambiguity (a single candle's high/low range
touches both) is a well-known backtesting problem — OHLC data alone can't
say which was hit first. Resolved here by dropping to the instrument's own
1-min candles covering that exact window and checking them in actual
sequence (1-min data exists regardless of which timeframe the prediction
came from); only if even a single 1-min candle is itself ambiguous does
this fall back to the conservative industry convention of assuming the
stop was hit first, so outcomes never overstate success.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

from activity_engine import ActivityEngine, FORMATION_LEVEL_FUNCS
from brokers.models import Candle
from sqlalchemy.exc import IntegrityError

from db.models import CandleToday, EngineSetting, PatternPrediction, SubscribedSymbol
from db.session import session_scope

logger = logging.getLogger(__name__)

# Middle of the "20-30 candles" outcome-scan window used throughout this
# project's backtesting-calibration notes — after this many candles with
# neither target nor stop hit, a pending prediction is closed out as
# "sideways" rather than left open forever.
OUTCOME_SCAN_CANDLES = 25

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

    def on_activities(self, symbol: str, exchange_segment: str, activities: List[dict]) -> int:
        """Call with exactly the list ActivityEngine.on_candle_closed just
        returned. Opens one PatternPrediction per tracked pattern found.
        Returns how many predictions were opened."""
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

            self._open_prediction(
                activity["instrument_id"], timeframe, pattern, direction,
                activity["ts"], entry, neckline, stop_loss, target,
            )
            opened += 1
        return opened

    def check_pending(self, symbol: str, exchange_segment: str, candle: Candle) -> int:
        """Call once per closed candle, regardless of whether anything
        fired on it. Resolves every pending prediction for this instrument/
        timeframe against the candle's own high/low. Returns how many
        predictions were resolved (target/stop hit, or timed out sideways)."""
        instrument_id = self._lookup_instrument_id(symbol, exchange_segment)
        if instrument_id is None:
            return 0
        resolved = 0
        with session_scope(self.session_factory) as session:
            pending = (
                session.query(PatternPrediction)
                .filter_by(instrument_id=instrument_id, timeframe=candle.timeframe, outcome=None)
                .filter(PatternPrediction.detected_ts < candle.timestamp)
                .all()
            )
            for pred in pending:
                if self._check_one(pred, symbol, exchange_segment, candle):
                    resolved += 1
        return resolved

    def _check_one(self, pred: PatternPrediction, symbol: str, exchange_segment: str, candle: Candle) -> bool:
        high, low = float(candle.high), float(candle.low)
        target, stop = float(pred.target), float(pred.stop_loss)
        if pred.direction == "bull":
            hit_target, hit_stop = high >= target, low <= stop
        else:
            hit_target, hit_stop = low <= target, high >= stop

        pred.candles_checked += 1

        if hit_target and hit_stop:
            outcome = self._resolve_same_candle_ambiguity(pred, symbol, exchange_segment, candle)
        elif hit_target:
            outcome = "target_hit"
        elif hit_stop:
            outcome = "stop_hit"
        elif pred.candles_checked >= OUTCOME_SCAN_CANDLES:
            outcome = "sideways"
        else:
            outcome = None

        if outcome is None:
            return False
        pred.outcome = outcome
        pred.outcome_ts = candle.timestamp
        return True

    def _resolve_same_candle_ambiguity(
        self, pred: PatternPrediction, symbol: str, exchange_segment: str, candle: Candle,
    ) -> str:
        if pred.timeframe == "1min":
            logger.warning(
                "Prediction %s: same 1-min candle hit both target and stop — no finer "
                "data available, assuming stop hit first (conservative default)", pred.id,
            )
            return "stop_hit"

        minutes = _TIMEFRAME_MINUTES.get(pred.timeframe, 1)
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
                target, stop = float(pred.target), float(pred.stop_loss)
                if pred.direction == "bull":
                    hit_target, hit_stop = high >= target, low <= stop
                else:
                    hit_target, hit_stop = low <= target, high >= stop
                if hit_target and hit_stop:
                    logger.warning(
                        "Prediction %s: even the 1-min candle at %s hit both target and "
                        "stop — assuming stop hit first (conservative default)", pred.id, row.ts,
                    )
                    return "stop_hit"
                if hit_target:
                    return "target_hit"
                if hit_stop:
                    return "stop_hit"

        logger.warning(
            "Prediction %s: coarser candle hit both target and stop but no 1-min candles "
            "were found for the window — assuming stop hit first (conservative default)", pred.id,
        )
        return "stop_hit"

    def _open_prediction(
        self, instrument_id: int, timeframe: str, pattern: str, direction: str,
        detected_ts, entry: float, neckline: Optional[float], stop_loss: float, target: float,
    ) -> None:
        """Idempotent on (instrument_id, timeframe, pattern, detected_ts) —
        re-processing the same candle (a replay re-run) must never open a
        duplicate prediction for a pattern that already fired on it."""
        try:
            with session_scope(self.session_factory) as session:
                session.add(PatternPrediction(
                    instrument_id=instrument_id, timeframe=timeframe, pattern=pattern, direction=direction,
                    detected_ts=detected_ts, entry_price=entry, neckline=neckline,
                    stop_loss=stop_loss, target=target,
                ))
        except IntegrityError:
            pass  # already recorded — a concurrent writer or a replay re-run

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
