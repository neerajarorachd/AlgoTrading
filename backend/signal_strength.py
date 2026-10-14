"""Signal strength (ALGO-6): how strong each raw signal is, as formulas in
the shared condition language, so EVENT(name) can tell a strong hammer from a
weak one straight from the candle data. Every formation is still recorded
and listed; only those meeting the minimums give a signal.

Measures are in % of price or ratios, so the same minimum works for any
stock. Minimums live in signal_strength_rules (system defaults from history,
user overrides); a pattern can override one for itself:
EVENT(hammer, range_pct=0.3). A signal with no measures always qualifies.

Each formula is evaluated ON THE EVENT'S OWN CANDLE (the candle it fired on).
"""
from __future__ import annotations

_VOLUME_RATIO = "VOLUME / mean(VOLUME, 20)"
_RANGE_PCT = "RANGE / CLOSE * 100"
_BODY_PCT = "BODY / CLOSE * 100"

STRENGTH_MEASURES = {
    # ---- single-candle ----
    "hammer": {"range_pct": _RANGE_PCT, "wick_ratio": "LOWER_WICK / BODY", "volume_ratio": _VOLUME_RATIO},
    "shooting_star": {"range_pct": _RANGE_PCT, "wick_ratio": "UPPER_WICK / BODY", "volume_ratio": _VOLUME_RATIO},
    "doji": {"range_pct": _RANGE_PCT, "volume_ratio": _VOLUME_RATIO},
    # ---- two-candle ----
    "bullish_engulfing": {"body_pct": _BODY_PCT, "engulf_ratio": "BODY / BODY[-1]", "volume_ratio": _VOLUME_RATIO},
    "bearish_engulfing": {"body_pct": _BODY_PCT, "engulf_ratio": "BODY / BODY[-1]", "volume_ratio": _VOLUME_RATIO},
    "piercing_line": {"body_pct": _BODY_PCT, "volume_ratio": _VOLUME_RATIO},
    "dark_cloud_cover": {"body_pct": _BODY_PCT, "volume_ratio": _VOLUME_RATIO},
    "tweezer_top": {"range_pct": _RANGE_PCT, "volume_ratio": _VOLUME_RATIO},
    "tweezer_bottom": {"range_pct": _RANGE_PCT, "volume_ratio": _VOLUME_RATIO},
    # ---- three-candle ----
    "morning_star": {"move_pct": "abs(CLOSE - OPEN[-2]) / OPEN[-2] * 100"},
    "evening_star": {"move_pct": "abs(CLOSE - OPEN[-2]) / OPEN[-2] * 100"},
    "three_white_soldiers": {"move_pct": "(CLOSE - OPEN[-2]) / OPEN[-2] * 100", "median_body_pct": "median(BODY, 3) / CLOSE * 100"},
    "three_black_crows": {"move_pct": "(OPEN[-2] - CLOSE) / OPEN[-2] * 100", "median_body_pct": "median(BODY, 3) / CLOSE * 100"},
    # ---- crossovers: how fast the lines separate ----
    "macd_bullish_cross": {"separation_pct": "abs(MACD_HIST - MACD_HIST[-1]) / CLOSE * 100"},
    "macd_bearish_cross": {"separation_pct": "abs(MACD_HIST - MACD_HIST[-1]) / CLOSE * 100"},
    "stoch_bullish_cross": {"gap": "abs(STOCH_K - STOCH_D)"},
    "stoch_bearish_cross": {"gap": "abs(STOCH_K - STOCH_D)"},
    # ---- Bollinger ----
    "bb_widening": {"width_change": "abs(BB_WIDTH - BB_WIDTH[-1])"},
    "bb_squeeze": {"width_change": "abs(BB_WIDTH - BB_WIDTH[-1])"},
}

# Measures in % of price grow with the candle's timeframe; ratios don't.
# Minimums are stored for 1-min (the base, user 2026-10-14) and multiplied
# for 3/5-min by TIMEFRAME_MULTIPLIER settings (measured from history).
PRICE_SCALED_MEASURES = {"range_pct", "body_pct", "move_pct", "median_body_pct", "separation_pct", "width_change"}


def scale_minimums(rules: dict, timeframe: str, multipliers: dict) -> dict:
    """1-min base minimums -> this timeframe's: price-% measures x the
    timeframe's multiplier (1 for 1-min or an unknown timeframe)."""
    factor = float(multipliers.get(timeframe, 1.0)) if timeframe != "1min" else 1.0
    return {signal: {m: (v * factor if m in PRICE_SCALED_MEASURES else v) for m, v in measures.items()}
            for signal, measures in rules.items()}
