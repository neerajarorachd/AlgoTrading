"""What a story row is about: each Subject reads the current candle (and a
few candles back) and names a state, a value and a rank value.

- state: e.g. "expanding"; the tracker turns a run of the same state into
  one story row (with an anti-flicker hold, see tracker.py).
- value: the number behind the state (BB width %, distance from VWAP %...),
  kept at the start and end of the run.
- rank: how strong the state is right now, used to sort the signal filter
  (bigger = stronger).

Thresholds are percentages (user decision 2026-10-30) and live in
DEFAULTS; P0 calibrates them on the research data. BB width and the
price-line gaps use an anchored band for "constant" (decided, see DEFAULTS).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

Obs = Optional[Tuple[str, float, float]]  # (state, value, rank)

DEFAULTS: Dict[str, float] = {
    "confirm_candles": 2,       # a new state must hold this many candles to count
    "lookback": 5,              # candles back for slopes and changes
    # BB width "constant" (user, 2026-10-30): settles when the width changed
    # < 10% over `lookback` candles; then stays constant while it is within
    # +-10% of the width it settled at (1.5% -> 1.35..1.65%). A break above
    # = expanding, below = squeezing. (5% was tried first: constant only 11%
    # of 5-min candles.)
    "bb_constant_pct": 10.0,
    "hug_pct": 0.10,            # price within 0.1% of a line = hugging
    "touch_pct": 0.05,          # a wick within 0.05% of a line = a touch
    "vwap_flat_pct": 0.05,      # VWAP moved < this % over lookback = flat
    "ma_flat_pct": 0.05,        # same for EMA50 / MA50
    # price-line gap "constant" (user, 2026-10-30): settles when the gap
    # changed < 15% over `lookback` candles; then stays constant within +-15%
    # of the settled gap, but never a band narrower than +-0.1 percentage
    # point (1.0% -> 0.85..1.15%; 0.2% -> 0.1..0.3%).
    "gap_change_pct": 15.0,
    "gap_band_min": 0.10,
    "volume_lookback": 20,
    "volume_high": 1.5,         # volume / its 20-candle average
    "volume_low": 0.6,
    "day_edge": 0.2,            # bottom / top 20% of today's range
}


class Ctx:
    """What a subject may look at: the current row, a short history, the
    day's range so far and the previous day's, swing structure, settings."""

    def __init__(self, settings: Dict[str, float]):
        self.settings = settings
        self.rows: List[dict] = []
        self.day_high = self.day_low = None
        self.prev_day_high = self.prev_day_low = None
        self.structure: Optional[str] = None
        self.last_leg_pct: Optional[float] = None
        self.vol_ratio: Optional[float] = None

    @property
    def row(self) -> dict:
        return self.rows[-1]

    def back(self, k: int) -> Optional[dict]:
        return self.rows[-1 - k] if len(self.rows) > k else None


def _pct(a, b):
    return (a - b) / b * 100 if b else None


class Subject:
    name = ""
    settings: Dict[str, float] = DEFAULTS  # the tracker gives each subject its own settings

    def observe(self, ctx: Ctx) -> Obs:
        raise NotImplementedError

    def accumulate(self, details: dict, row: dict, state: str) -> None:
        """Optional per-candle details kept on the run (touches...)."""

    def new_day(self) -> None:
        """Called on the first candle of a trading day."""


class AnchoredBand(Subject):
    """"constant" with memory: once the value settles (changed less than
    `settle` % over the lookback), remember it and stay constant while the
    value stays inside a band around it. A break of the band is movement."""

    anchor: Optional[float] = None

    def anchored(self, value, change_pct, settle_pct, half_band, up, down):
        if self.anchor is not None:
            if value > self.anchor + half_band(self.anchor):
                self.anchor = None
                return up
            if value < self.anchor - half_band(self.anchor):
                self.anchor = None
                return down
            return "constant"
        if abs(change_pct) < settle_pct:
            self.anchor = value
            return "constant"
        return up if change_pct > 0 else down


class LineSlope(Subject):
    def __init__(self, name, field, flat_key):
        self.name, self.field, self.flat_key = name, field, flat_key

    def observe(self, ctx):
        n = int(ctx.settings["lookback"])
        now, old = ctx.row.get(self.field), (ctx.back(n) or {}).get(self.field)
        if now is None or old is None:
            return None
        s = _pct(now, old)
        flat = ctx.settings[self.flat_key]
        state = "rising" if s > flat else "falling" if s < -flat else "flat"
        return state, s, abs(s)


class PriceVsLine(Subject):
    """above / below / hugging a line; counts touches and rejections."""

    def __init__(self, name, field):
        self.name, self.field = name, field

    def observe(self, ctx):
        line = ctx.row.get(self.field)
        if line is None:
            return None
        d = _pct(ctx.row["close"], line)
        hug = ctx.settings["hug_pct"]
        state = "hugging" if abs(d) <= hug else "above" if d > 0 else "below"
        return state, d, abs(d)

    def accumulate(self, details, row, state):
        line = row.get(self.field)
        if line is None:
            return
        tol = line * self.settings["touch_pct"] / 100
        if row["low"] - tol <= line <= row["high"] + tol:
            details["touches"] = details.get("touches", 0) + 1
            # a rejection: touched, but closed back on the run's side
            if (state == "above" and row["close"] > line) or (state == "below" and row["close"] < line):
                details["rejections"] = details.get("rejections", 0) + 1
        d = abs(_pct(row["close"], line) or 0)
        details["max_dist_pct"] = round(max(details.get("max_dist_pct", 0.0), d), 4)


class LineGap(AnchoredBand):
    """Is price moving away from the line (expanding), back to it (narrowing)
    or keeping its distance (constant)? Anchored band, see DEFAULTS."""

    def __init__(self, name, field):
        self.name, self.field = name, field
        self.side = None

    def new_day(self):
        self.anchor = self.side = None  # VWAP restarts each day

    def observe(self, ctx):
        n = int(ctx.settings["lookback"])
        old = ctx.back(n)
        line = ctx.row.get(self.field)
        if old is None or line is None or old.get(self.field) is None:
            return None
        signed = _pct(ctx.row["close"], line)
        side = 1 if signed >= 0 else -1
        if side != self.side:  # price crossed the line: the old settled gap no longer applies
            self.anchor, self.side = None, side
        g_now = abs(signed)
        g_old = side * _pct(old["close"], old[self.field])  # negative if price was on the other side
        floor = ctx.settings["gap_band_min"]
        rel = (g_now - g_old) / max(abs(g_old), floor) * 100
        lim = ctx.settings["gap_change_pct"]
        state = self.anchored(g_now, rel, lim, lambda a: max(a * lim / 100, floor), "expanding", "narrowing")
        return state, g_now, abs(rel)


class BBWidth(AnchoredBand):
    name = "bb_width"

    def __init__(self):
        self.anchor = None

    def observe(self, ctx):
        n = int(ctx.settings["lookback"])
        r, old = ctx.row, ctx.back(n)
        if r.get("bb_middle") is None or old is None or old.get("bb_middle") is None:
            return None
        w = (r["bb_upper"] - r["bb_lower"]) / r["bb_middle"] * 100
        w_old = (old["bb_upper"] - old["bb_lower"]) / old["bb_middle"] * 100
        if not w_old:
            return None
        ch = (w / w_old - 1) * 100
        lim = ctx.settings["bb_constant_pct"]
        state = self.anchored(w, ch, lim, lambda a: a * lim / 100, "expanding", "squeezing")
        return state, w, abs(ch)


class PriceVsBB(Subject):
    name = "price_vs_bb"

    def observe(self, ctx):
        r = ctx.row
        if r.get("bb_middle") is None:
            return None
        c, up, mid, lo = r["close"], r["bb_upper"], r["bb_middle"], r["bb_lower"]
        state = ("above_upper" if c > up else "below_lower" if c < lo
                 else "upper_half" if c >= mid else "lower_half")
        pb = (c - lo) / (up - lo) if up > lo else 0.5  # %B: 0 at lower band, 1 at upper
        return state, pb, abs(pb - 0.5)

    def accumulate(self, details, row, state):
        if row.get("bb_upper") is None:
            return
        if row["high"] >= row["bb_upper"]:
            details["upper_touches"] = details.get("upper_touches", 0) + 1
        if row["low"] <= row["bb_lower"]:
            details["lower_touches"] = details.get("lower_touches", 0) + 1


class MaCross(Subject):
    name = "ma21_vs_ma50"

    def observe(self, ctx):
        a, b = ctx.row.get("ma21"), ctx.row.get("ma50")
        if a is None or b is None:
            return None
        d = _pct(a, b)
        return ("above" if d >= 0 else "below"), d, abs(d)


class Macd(Subject):
    """MACD vs its signal line, and whether the gap is growing: the anchor of
    setup evaluation (user, 2026-10-30)."""
    name = "macd"

    def observe(self, ctx):
        r, p = ctx.row, ctx.back(1)
        if r.get("macd_line") is None or r.get("macd_signal") is None or not p or p.get("macd_signal") is None:
            return None
        d, dp = r["macd_line"] - r["macd_signal"], p["macd_line"] - p["macd_signal"]
        side = "bull" if d >= 0 else "bear"
        widening = abs(d) >= abs(dp) and (d >= 0) == (dp >= 0)
        state = f"{side}_{'widening' if widening else 'narrowing'}"
        v = d / r["close"] * 100
        return state, v, abs(v)


class MacdZero(Subject):
    name = "macd_zero"

    def observe(self, ctx):
        m = ctx.row.get("macd_line")
        if m is None:
            return None
        v = m / ctx.row["close"] * 100
        return ("above" if m >= 0 else "below"), v, abs(v)


class Zone(Subject):
    def __init__(self, name, field, edges, labels):
        self.name, self.field, self.edges, self.labels = name, field, edges, labels

    def observe(self, ctx):
        v = ctx.row.get(self.field)
        if v is None:
            return None
        for edge, label in zip(self.edges, self.labels):
            if v < edge:
                return label, v, v
        return self.labels[-1], v, v


class StochCross(Subject):
    name = "stoch_cross"

    def observe(self, ctx):
        k, d = ctx.row.get("stoch_k"), ctx.row.get("stoch_d")
        if k is None or d is None:
            return None
        return ("k_over_d" if k >= d else "k_under_d"), k - d, abs(k - d)


class Adx(Subject):
    name = "adx"

    def observe(self, ctx):
        a, p, m = ctx.row.get("adx"), ctx.row.get("plus_di"), ctx.row.get("minus_di")
        if a is None or p is None or m is None:
            return None
        zone = "lt20" if a < 20 else "20_30" if a < 30 else "30_40" if a < 40 else "40p"
        return f"{zone}_{'plus' if p >= m else 'minus'}", a, a


class Volume(Subject):
    name = "volume"

    def observe(self, ctx):
        if ctx.vol_ratio is None:
            return None
        r = ctx.vol_ratio
        s = ctx.settings
        state = "high" if r >= s["volume_high"] else "drying" if r <= s["volume_low"] else "normal"
        return state, r, r


class Structure(Subject):
    name = "structure"

    def observe(self, ctx):
        if ctx.structure is None:
            return None
        leg = abs(ctx.last_leg_pct or 0.0)
        return ctx.structure, leg, leg


class DayRange(Subject):
    name = "day_range"

    def observe(self, ctx):
        hi, lo = ctx.day_high, ctx.day_low
        if hi is None or hi <= lo:
            return None
        pos = (ctx.row["close"] - lo) / (hi - lo)
        e = ctx.settings["day_edge"]
        state = "near_high" if pos >= 1 - e else "near_low" if pos <= e else "middle"
        return state, pos, abs(pos - 0.5)


class PrevDay(Subject):
    name = "prev_day"

    def observe(self, ctx):
        hi, lo = ctx.prev_day_high, ctx.prev_day_low
        if hi is None:
            return None
        c = ctx.row["close"]
        if c > hi:
            return "above_high", _pct(c, hi), abs(_pct(c, hi))
        if c < lo:
            return "below_low", _pct(c, lo), abs(_pct(c, lo))
        return "inside", (c - lo) / (hi - lo) if hi > lo else 0.5, 0.0


def default_subjects() -> List[Subject]:
    """The P0 set: VWAP first, then BB, EMA50/MA50/MA21, momentum, volume,
    structure and day levels."""
    return [
        LineSlope("vwap_slope", "vwap", "vwap_flat_pct"),
        PriceVsLine("price_vs_vwap", "vwap"),
        LineGap("vwap_gap", "vwap"),
        BBWidth(),
        PriceVsBB(),
        LineSlope("ema50_slope", "ema50", "ma_flat_pct"),
        PriceVsLine("price_vs_ema50", "ema50"),
        LineGap("ema50_gap", "ema50"),
        LineSlope("ma50_slope", "ma50", "ma_flat_pct"),
        PriceVsLine("price_vs_ma50", "ma50"),
        PriceVsLine("price_vs_ma21", "ma21"),
        MaCross(),
        Macd(),
        MacdZero(),
        Zone("rsi", "rsi", (30, 40, 60, 70), ("lt30", "30_40", "40_60", "60_70", "70p")),
        Zone("stoch_zone", "stoch_k", (20, 80), ("oversold", "middle", "overbought")),
        StochCross(),
        Adx(),
        Volume(),
        Structure(),
        DayRange(),
        PrevDay(),
    ]
