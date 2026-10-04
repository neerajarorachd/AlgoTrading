"""Expected-move / suggested SL+target for the Market Watch buy/sell popup —
second slice, explicit instruction 2026-10-04: "You should write down space
finding formula for each pattern... we had calculated the range in
backtesting. we can find the range from backtest data for a particular time
frame too. get the minimum range from the above 2 options."

Two independent ways to estimate how far price can plausibly move in the
signal's own direction, per instrument/pattern:

1. GEOMETRIC space -- a real, currently-observable chart level, different per
   pattern family (see geometric_target_distance's own docstring for the
   formula table). None when the pattern has no inherent level of its own.
2. BACKTESTED range -- the real historical median favorable move for this
   EXACT (instrument, pattern, timeframe), via LibPatternOutcomes.
   pattern_level_analysis (already built, nothing new here) -- direction-
   aware per PatternOutcome's own convention (max_favorable_pct is always
   "the highest price reached," so for a BEAR pattern the pattern's own
   favorable direction is actually max_adverse_pct, not max_favorable_pct;
   see PatternOutcome's own docstring and pattern_outcome_analysis.py's
   literal max()/min() computation).

The target distance is min() of whichever of the two are available --
"get the minimum range from the above 2 options," literally. When NEITHER
is available (a pattern with no geometry of its own and no PatternOutcome
history yet), falls back to the existing ATR-based
prediction_tracker.crossover_target/crossover_stop_loss -- the same
fallback candlestick/indicator patterns already use everywhere else in
this project, not a third ranked candidate squeezed into the same min().

Stop-loss stays ATR-based universally (prediction_tracker.
crossover_stop_loss) regardless of pattern -- a protective stop is a risk
decision, not a "how far can this move" one; this project's own existing
FORMATION_LEVEL_FUNCS precedent ties SL to the SAME geometry as target only
for graph formations specifically, which aren't handled geometrically here
(see graph_formation note below), so there's no existing precedent this
would contradict by staying uniform.

Suggested quantity: last 15-min average volume / LIQUIDITY_DIVISOR_DEFAULT
-- a hand-picked starting divisor (not reusing Strategy.
liquidity_safety_divisor, which has no universal default, only a per-
Strategy opt-in value), same "ship a sensible default, refine from real
data later" posture as the rest of this project's calibration work.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from db.models import CandleIndicators, InstrumentActivity
from db.ops.LibPatternOutcomes import pattern_level_analysis
from guiding_scenarios import WINDOW_LOOKBACK_DAYS
from prediction_tracker import crossover_stop_loss, crossover_target

# Geometric space formulas only exist for these pattern families today --
# see geometric_target_distance. Every other pattern (candlestick, plain
# indicator crossovers, graph formations without a persisted neckline --
# see note below) has no geometry of its own and relies on backtested
# range / ATR only.
_VWAP_REJECTION_PATTERNS = {
    "vwap_rejection_bull", "vwap_rejection_bull_strong",
    "vwap_rejection_bear", "vwap_rejection_bear_strong",
}
_STRUCTURE_PATTERNS = {
    "bullish_structure_shift", "bullish_break_of_structure",
    "bearish_structure_shift", "bearish_break_of_structure",
}

# double_top/double_bottom/triple_top/triple_bottom DO have a real neckline-
# based FORMATION_LEVEL_FUNCS geometry in activity_engine.py -- but it needs
# the pattern's own last-3 SwingPoints, which only exist in the live
# engine's in-memory state at detection time, never persisted anywhere
# queryable after the fact. Reconstructing them retroactively from
# InstrumentActivity's swing_high/swing_low history is a real, separate
# piece of work (not attempted here) -- these patterns fall back to
# backtested range / ATR only, same as candlesticks, until that's built.

LIQUIDITY_DIVISOR_DEFAULT = 20

DEFAULT_ATR_MULTIPLIER = 1.5
DEFAULT_RISK_REWARD_RATIO = 2.0


def latest_indicator_row(session, instrument_id: int, timeframe: str) -> Optional[CandleIndicators]:
    """Shared with watch_order_popup.py, which needs the same row for ATR."""
    return (
        session.query(CandleIndicators)
        .filter_by(instrument_id=instrument_id, timeframe=timeframe)
        .order_by(CandleIndicators.ts.desc()).first()
    )


def geometric_target_distance(session, instrument_id: int, timeframe: str,
                               activity: InstrumentActivity, direction: str, ltp: float) -> Optional[float]:
    """Pattern-specific price distance (always positive, or None) to a real,
    currently-observable chart level:

    - VWAP rejection (+ strong variants): distance from LTP to the
      Bollinger Band on the expected side -- bull targets bb_upper, bear
      targets bb_lower. None if BB isn't populated yet (warm-up period).
    - Structure (break of structure / structure shift): distance from LTP
      to the confirming swing's own price -- the triggering
      InstrumentActivity row's stored high_price (bull, the HH) or
      low_price (bear, the LL); same "confirming candle's own OHLC"
      convention order_backtest.py already reads for these pattern types.
    - Everything else: None (no geometry of this kind applies)."""
    pattern = activity.activity
    if pattern in _VWAP_REJECTION_PATTERNS:
        row = latest_indicator_row(session, instrument_id, timeframe)
        if row is None or row.bb_upper is None or row.bb_lower is None:
            return None
        return float(row.bb_upper) - ltp if direction == "bull" else ltp - float(row.bb_lower)
    if pattern in _STRUCTURE_PATTERNS:
        swing_price = float(activity.high_price) if direction == "bull" else float(activity.low_price)
        return abs(swing_price - ltp)
    return None


def backtested_target_distance(session, instrument_id: int, timeframe: str, pattern: str,
                                direction: str, ltp: float) -> Optional[float]:
    """Real historical median favorable move for this exact (instrument,
    pattern, timeframe), converted from a % to a price distance using
    today's LTP as the scale (an approximation -- the real historical
    moves were each a % of THEIR OWN entry price, not today's; applying
    today's price is the standard way to project a historical % forward).
    None when there's no PatternOutcome history yet, or the relevant
    median is None (e.g. count too small -- _band_stats still returns
    None rather than a median of nothing)."""
    now = datetime.now(timezone.utc)
    stats = pattern_level_analysis(
        session, instrument_id, [timeframe], now - timedelta(days=WINDOW_LOOKBACK_DAYS["2y"]), now, pattern,
    )
    if stats is None:
        return None
    # See module docstring: max_favorable_pct is always "highest price
    # reached" regardless of the pattern's own direction, so a BEAR
    # pattern's own favorable move is actually max_adverse_pct (typically
    # negative -- a move down), not max_favorable_pct.
    pct = stats["up_median_pct"] if direction == "bull" else stats["down_median_pct"]
    if pct is None:
        return None
    return abs(pct) * ltp


def suggested_sl_and_target(session, instrument_id: int, timeframe: str, activity: InstrumentActivity,
                             direction: str, ltp: float, atr: Optional[float],
                             atr_multiplier: float = DEFAULT_ATR_MULTIPLIER,
                             risk_reward_ratio: float = DEFAULT_RISK_REWARD_RATIO) -> dict:
    """{sl_price, target_price, target_source} -- target_source is "geometric"/
    "backtested"/"atr_fallback" so a caller (and a curious user) can see
    WHICH of the two real options won the min(), or that neither applied.
    None/None when ATR itself isn't available yet (too early in the
    instrument's life) -- same "don't guess, say so" posture as everywhere
    else in this module."""
    sl_price = crossover_stop_loss(ltp, atr, direction, atr_multiplier) if atr is not None else None

    geo = geometric_target_distance(session, instrument_id, timeframe, activity, direction, ltp)
    backtested = backtested_target_distance(session, instrument_id, timeframe, activity.activity, direction, ltp)
    candidates = [(d, name) for d, name in ((geo, "geometric"), (backtested, "backtested")) if d is not None and d > 0]

    if candidates:
        distance, source = min(candidates, key=lambda c: c[0])
    elif atr is not None:
        distance = atr * atr_multiplier * risk_reward_ratio
        source = "atr_fallback"
    else:
        distance, source = None, None

    target_price = None
    if distance is not None:
        target_price = ltp + distance if direction == "bull" else ltp - distance

    return {"sl_price": sl_price, "target_price": target_price, "target_source": source}


def suggested_quantity(avg_volume_last_15min: Optional[float],
                        divisor: float = LIQUIDITY_DIVISOR_DEFAULT) -> Optional[int]:
    if not avg_volume_last_15min:
        return None
    return max(1, int(avg_volume_last_15min / divisor))
