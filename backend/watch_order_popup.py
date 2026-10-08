"""Market Watch buy/sell suggestion popup. First slice (2026-10-04): LTP,
average volume (day vs. last 15 min), and for single/multi-candle patterns
the intensity-related backtested range. Second slice (2026-10-05): real
suggested SL/target (expected_move.py's geometric-space-vs-backtested-range
minimum, ATR fallback) and a suggested quantity.

LTP: the frontend already holds it live via its own WebSocket tick stream
(MarketWatch.jsx's `liveTicks`) and passes it through as a query param
(routes_watch_popup.py) -- this module trusts that value when given (it's
genuinely more current than anything queryable here) and only falls back
to the latest stored candle's own close when no live price was supplied
(e.g. a caller other than the Watch page itself).

Display-only, same explicit scope as the rest of the Watch page work: no
order placement from here, that stays in the separate Trading system.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Optional

import watch_scoring
from db.models import InstrumentActivity, PatternDefinition, SubscribedSymbol
from db.ops import LibCandles
from db.ops.LibPatternOutcomes import intensity_banded_analysis
from expected_move import latest_indicator_row, suggested_quantity, suggested_sl_and_target
from guiding_scenarios import WINDOW_LOOKBACK_DAYS
from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

# Volume is compared at 1-min granularity regardless of which timeframe the
# triggering pattern fired on -- same "average volume over recent candles"
# mental model this project already uses for the live entry gate (see
# Strategy.min_avg_volume_multiple/min_avg_volume_lookback), just applied
# here for display instead of as a live order-entry gate.
VOLUME_TIMEFRAME = "1min"
LAST_N_MINUTES = 15

# single_candle/multi_candle are the only kinds with an intensity formula
# today (InstrumentActivity.intensity's own docstring: "NULL for a pattern
# with no defined intensity formula yet") -- matches exactly.
_INTENSITY_KINDS = {"single_candle", "multi_candle"}


def latest_directional_activity(
    session, instrument_id: int, timeframe: str, direction: Optional[str] = None,
) -> Optional[InstrumentActivity]:
    """Most recent directional activity for this instrument/timeframe.
    `direction` ("bull"/"bear") restricts to just that side's pattern set
    -- None (the default) means "most recent regardless of direction,"
    used only by the two dedicated direction-blind tests below; the real
    caller (order_popup_data) ALWAYS passes a direction, to stay
    consistent with the colored-cell grid's own windowed bull/bear score
    (see its own docstring for why)."""
    if direction == "bull":
        patterns = BULLISH_PATTERNS
    elif direction == "bear":
        patterns = BEARISH_PATTERNS
    else:
        patterns = BULLISH_PATTERNS | BEARISH_PATTERNS
    return (
        session.query(InstrumentActivity)
        .filter(
            InstrumentActivity.instrument_id == instrument_id, InstrumentActivity.timeframe == timeframe,
            InstrumentActivity.activity.in_(patterns),
        )
        .order_by(InstrumentActivity.ts.desc()).first()
    )


def volume_stats(session, symbol: str, exchange_segment: str) -> dict:
    """avg_volume_day: mean 1-min candle volume over every candle recorded
    today so far. avg_volume_last_15min: the same, over just the most
    recent LAST_N_MINUTES rows -- comparing the two is the actual point
    (a last-15-min average well above the day's own average is a real
    volume pickup, not just "today happened to be a busy day")."""
    candles = LibCandles.get_range(session, symbol, exchange_segment, VOLUME_TIMEFRAME)
    if not candles:
        return {"avg_volume_day": None, "avg_volume_last_15min": None}
    day_volumes = [c.volume for c in candles]
    recent_volumes = day_volumes[-LAST_N_MINUTES:]
    return {
        "avg_volume_day": mean(day_volumes),
        "avg_volume_last_15min": mean(recent_volumes),
    }


def intensity_band_for(session, instrument_id: int, timeframe: str, pattern: str, intensity: float) -> Optional[dict]:
    """Finds the historical intensity band (Low/Mid/High tercile, see
    LibPatternOutcomes.intensity_banded_analysis) THIS occurrence's own
    intensity value falls into, and returns that band's real backtested
    up_median_pct/down_median_pct/range_median_pct -- "relate the intensity
    with the range" by reusing the exact banding this project already
    built for offline analysis, not a new formula. None if there's no
    PatternOutcome history for this pattern yet, or this intensity falls
    outside every band's observed range (can happen at the extremes with a
    small sample)."""
    now = datetime.now(timezone.utc)
    bands = intensity_banded_analysis(
        session, instrument_id, [timeframe], now - timedelta(days=WINDOW_LOOKBACK_DAYS["2y"]), now, pattern,
    )
    for band in bands:
        if band["intensity_min"] <= intensity <= band["intensity_max"]:
            return band
    return None


def _resolve_ltp(session, instrument, ltp: Optional[float]) -> Optional[float]:
    if ltp is not None:
        return ltp
    candles = LibCandles.get_range(session, instrument.symbol, instrument.exchange_segment, VOLUME_TIMEFRAME)
    return float(candles[-1].close_price) if candles else None


def order_popup_data(session, instrument_id: int, timeframe: str = "3min", ltp: Optional[float] = None,
                     engine=None) -> Optional[dict]:
    """None when this instrument has no directional signal to show a
    buy/sell suggestion for at all -- the caller (route) turns that into a
    404/empty response, not a half-filled popup.

    Direction is resolved via watch_scoring.score_instrument -- the EXACT
    same windowed bull/bear score the colored-cell grid and the Market
    Watch row's own Buy/Sell button use -- not independently re-derived
    from "whatever single activity happened to fire most recently."
    Real bug found 2026-10-04 browser-verifying this: a row's button said
    "Sell" (bear-dominant over the scoring window) while the popup it
    opened said "BUY", because the popup was picking direction off the
    single latest activity regardless of which side was actually winning
    over the window -- those two numbers can legitimately disagree. Tied
    scores (including 0-0, "quiet") return None here, matching the
    frontend's own button, which doesn't render at all when there's no
    clear lean."""
    instrument = session.get(SubscribedSymbol, instrument_id)
    if instrument is None:
        return None

    # `engine`: same live (unflushed) activity the grid's button scores
    # with, so the popup's direction can't disagree with the button that
    # opened it (see routes_watch_scores.py)
    score = watch_scoring.score_instrument(
        session, instrument_id, timeframe, watch_scoring.WINDOW_CANDLES, watch_scoring.load_pattern_weights(session),
        engine=engine)
    if score["bull_score"] == score["bear_score"]:
        return None
    direction = "bull" if score["bull_score"] > score["bear_score"] else "bear"

    activity = latest_directional_activity(session, instrument_id, timeframe, direction)
    if activity is None:
        return None

    kind = (
        session.query(PatternDefinition.kind).filter_by(code=activity.activity).scalar()
    )

    intensity_band = None
    if kind in _INTENSITY_KINDS and activity.intensity is not None:
        intensity_band = intensity_band_for(
            session, instrument_id, timeframe, activity.activity, float(activity.intensity))

    volume = volume_stats(session, instrument.symbol, instrument.exchange_segment)

    resolved_ltp = _resolve_ltp(session, instrument, ltp)
    sl_target = {"sl_price": None, "target_price": None, "target_source": None}
    quantity = None
    if resolved_ltp is not None:
        indicator_row = latest_indicator_row(session, instrument_id, timeframe)
        atr = float(indicator_row.atr) if indicator_row is not None and indicator_row.atr is not None else None
        sl_target = suggested_sl_and_target(session, instrument_id, timeframe, activity, direction, resolved_ltp, atr)
        quantity = suggested_quantity(volume["avg_volume_last_15min"])

    return {
        "pattern": activity.activity, "direction": direction, "kind": kind,
        "intensity": float(activity.intensity) if activity.intensity is not None else None,
        "detected_ts": activity.ts.replace(tzinfo=timezone.utc).isoformat(),
        "intensity_band": intensity_band,
        "ltp": resolved_ltp, "suggested_quantity": quantity,
        **sl_target,
        **volume,
    }
