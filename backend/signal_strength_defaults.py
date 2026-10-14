"""Starting signal-strength minimums (ALGO-6), seeded at startup by
LibSignalStrength.seed_defaults -- every value here is only the first value:
all of it is configurable and recalibrated from history through
signal_strength_calibration (API: POST /api/signal-strength/calibrate).

Calibrated 2026-10-14 from the last 20 sessions of all 13 stocks at 1-min
(the base): each signal's minimums are set together so ~40% of its
formations qualify overall. 3/5-min minimums are the base x the measured
timeframe multipliers (price-% measures only)."""
from __future__ import annotations

SIGNAL_STRENGTH_BASE = {
    'hammer': {'range_pct': 0.0528, 'wick_ratio': 2.6667, 'volume_ratio': 0.3676},
    'shooting_star': {'range_pct': 0.0563, 'wick_ratio': 2.6667, 'volume_ratio': 0.3749},
    'doji': {'range_pct': 0.0593, 'volume_ratio': 0.5232},
    'bullish_engulfing': {'body_pct': 0.0465, 'engulf_ratio': 1.6667, 'volume_ratio': 0.4461},
    'bearish_engulfing': {'body_pct': 0.046, 'engulf_ratio': 1.75, 'volume_ratio': 0.4824},
    'piercing_line': {'body_pct': 0.0618, 'volume_ratio': 0.6937},
    'dark_cloud_cover': {'body_pct': 0.0593, 'volume_ratio': 0.7239},
    'tweezer_top': {'range_pct': 0.0741, 'volume_ratio': 0.5282},
    'tweezer_bottom': {'range_pct': 0.0717, 'volume_ratio': 0.4964},
    'morning_star': {'move_pct': 0.0396},
    'evening_star': {'move_pct': 0.0306},
    'three_white_soldiers': {'move_pct': 0.2078, 'median_body_pct': 0.0789},
    'three_black_crows': {'move_pct': 0.1591, 'median_body_pct': 0.065},
    'macd_bullish_cross': {'separation_pct': 0.0067},
    'macd_bearish_cross': {'separation_pct': 0.0068},
    'stoch_bullish_cross': {'gap': 5.5941},
    'stoch_bearish_cross': {'gap': 5.808},
    'bb_widening': {'width_change': 0.0324},
    'bb_squeeze': {'width_change': 0.0173},
}

# SystemSetting rows (key, default, description)
STRENGTH_SETTING_SEED = (
    ("strength_multiplier_3min", 1.79,
     "3-min minimums for price-% strength measures = 1-min minimum x this (measured: median 3-min candle range / 1-min)"),
    ("strength_multiplier_5min", 2.36,
     "5-min minimums for price-% strength measures = 1-min minimum x this (measured: median 5-min candle range / 1-min)"),
    ("strength_target_share", 0.40,
     "Calibration target: share of each signal's formations that should qualify as strong (0.40 = the stronger 40%)"),
    ("strength_calibration_sessions", 20,
     "Calibration history: how many recent sessions of all watched stocks to calibrate from"),
)
