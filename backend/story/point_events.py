"""Point events added in market story P1 (plan: docs/market_story_plan.md).

Pure functions; ActivityEngine calls them per closed candle when the engine
setting `point_events_v2` is on (off by default).

Line tests, for VWAP, BB upper/middle/lower, MA21, MA50 and EMA50 (user,
2026-10-30: "same as BB expansion"). A line test is one episode, not an
event per candle:
- it starts the first time price reaches the zone around the line (the line
  +- zone %) coming from one side: <line>_test_from_above / _from_below;
- while closes stay inside the zone it is the same test (other signals keep
  being recorded as usual); touches and the deepest push through the line
  are counted;
- it ends when a close leaves the zone: back out on the side it came from =
  <line>_bounce_up / _bounce_down (the line held), out the other side =
  <line>_break_up / _break_down. The exit is what the story and the
  prediction unit act on. Intensity on an exit = candles the test lasted.
(Named "bounce" so they never clash with the existing vwap_rejection_bull /
_bear, which are a different, two-candle rule.)

Day levels:
- day_high_break / day_low_break: a new high / low for the day so far;
- prev_day_high_break / prev_day_low_break: the close crossed above the
  previous day's high / below its low.

Intensity is the close's distance from the line or level, in %.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

LINES = ("vwap", "bb_upper", "bb_middle", "bb_lower", "ma21", "ma50", "ema50")
LINE_LABELS = {"vwap": "VWAP", "bb_upper": "upper Bollinger Band", "bb_middle": "middle Bollinger Band",
               "bb_lower": "lower Bollinger Band", "ma21": "MA21", "ma50": "MA50", "ema50": "EMA50"}
DEFAULT_ZONE_PCT = 0.10  # zone around a line, % of the line (user: 0.05-0.10%, calibrated per stock)
DEFAULT_ARM_MULT = 3.0   # a new test needs a close this many zones away from the line first
# Per-stock zone (2026-10-30): 0.3 x the stock's own ATR lands on the user's
# 0.05-0.10% range on 5-min (RELIANCE ~0.049%, HINDCOPPER ~0.100% over 2
# years); DEFAULT_ZONE_PCT is used until ATR has warmed up.
DEFAULT_ZONE_ATR = 0.30

Event = Tuple[str, str, Optional[float]]  # (activity_type, activity, intensity)


def _dist(close: float, level: float) -> float:
    return abs(close - level) / level * 100 if level else 0.0


class LineTest:
    """One line's test state for one instrument + timeframe.

    A new test needs an approach: price must first close at least `arm_pct`
    away from the line (armed); only then does reaching the zone start a
    test. After a test ends it is disarmed until price moves away again, so
    price drifting next to a line is one test, not one per candle."""

    __slots__ = ("side", "armed", "active", "from_side", "candles", "touches", "deepest")

    def __init__(self):
        self.side = None      # +1 above the zone, -1 below, None not known yet
        self.armed = False
        self.active = False
        self.from_side = None
        self.candles = self.touches = 0
        self.deepest = 0.0    # deepest push through the line against from_side, %

    def step(self, name: str, line: float, high: float, low: float, close: float,
             zone_pct: float, arm_pct: float = 0.0) -> List[Event]:
        out: List[Event] = []
        z = line * zone_pct / 100
        if not self.active:
            if self.side is not None and self.armed:
                reached = low <= line + z if self.side > 0 else high >= line - z
                if reached:
                    self.active, self.from_side = True, self.side
                    self.candles = self.touches = 0
                    self.deepest = 0.0
                    out.append(("line", f"{name}_test_from_{'above' if self.side > 0 else 'below'}",
                                _dist(close, line)))
            if not self.active:
                new_side = 1 if close > line + z else -1 if close < line - z else self.side
                if new_side != self.side:  # e.g. a gap past the zone: a fresh approach is needed
                    self.side, self.armed = new_side, False
                if self.side is not None and _dist(close, line) >= max(arm_pct, zone_pct):
                    self.armed = True
                return out
        # inside a test
        self.candles += 1
        if low - z <= line <= high + z:
            self.touches += 1
        push = (line - low) if self.from_side > 0 else (high - line)
        self.deepest = max(self.deepest, push / line * 100 if line else 0.0)
        exit_side = 1 if close > line + z else -1 if close < line - z else 0
        if exit_side:
            kind = "bounce" if exit_side == self.from_side else "break"
            out.append(("line", f"{name}_{kind}_{'up' if exit_side > 0 else 'down'}", float(self.candles)))
            self.active, self.side = False, exit_side
            self.armed = _dist(close, line) >= max(arm_pct, zone_pct)  # a strong exit is already away
        return out

    def state(self) -> Optional[str]:
        if self.active:
            return "testing"
        return None if self.side is None else ("above" if self.side > 0 else "below")


class LineTests:
    """All lines' tests for one instrument + timeframe. Reset each day (VWAP
    restarts and the open can gap past a line).

    Zone: a fixed % of price (`zone_pct`), or, when `zone_atr` is set, that
    fraction of the stock's own ATR (so it differs stock to stock, as the
    user expects). A new test needs price to have closed `arm_mult` zones
    away from the line first."""

    def __init__(self, zone_pct: float = DEFAULT_ZONE_PCT, arm_mult: float = DEFAULT_ARM_MULT,
                 zone_atr: Optional[float] = DEFAULT_ZONE_ATR):
        self.zone_pct, self.arm_mult, self.zone_atr = zone_pct, arm_mult, zone_atr
        self.tests = {name: LineTest() for name in LINES}
        self.day = None

    def zone_for(self, row: dict) -> float:
        if self.zone_atr and row.get("atr") and row.get("close"):
            return self.zone_atr * row["atr"] / row["close"] * 100
        return self.zone_pct

    def update(self, row: dict, day=None) -> List[Event]:
        if day is not None and day != self.day:
            self.day = day
            self.tests = {name: LineTest() for name in LINES}
        z = self.zone_for(row)
        out: List[Event] = []
        for name in LINES:
            line = row.get(name)
            if line:
                out += self.tests[name].step(name, line, row["high"], row["low"], row["close"], z, z * self.arm_mult)
        return out


class DayLevels:
    """Per instrument + timeframe: today's high/low so far and the previous
    day's, for the day-level events."""

    __slots__ = ("day", "high", "low", "prev_high", "prev_low", "prev_close")

    def __init__(self):
        self.day = self.high = self.low = self.prev_high = self.prev_low = self.prev_close = None

    def update(self, day, high: float, low: float, close: float) -> List[Event]:
        out: List[Event] = []
        if day != self.day:
            if self.day is not None:
                self.prev_high, self.prev_low = self.high, self.low
            self.day, self.high, self.low = day, None, None
        if self.high is not None and high > self.high:
            out.append(("level", "day_high_break", _dist(high, self.high)))
        if self.low is not None and low < self.low:
            out.append(("level", "day_low_break", _dist(low, self.low)))
        pc = self.prev_close
        if self.prev_high is not None and pc is not None and pc <= self.prev_high < close:
            out.append(("level", "prev_day_high_break", _dist(close, self.prev_high)))
        if self.prev_low is not None and pc is not None and pc >= self.prev_low > close:
            out.append(("level", "prev_day_low_break", _dist(close, self.prev_low)))
        self.high = high if self.high is None else max(self.high, high)
        self.low = low if self.low is None else min(self.low, low)
        self.prev_close = close
        return out


SWING_LABEL_EVENTS = {"HH": "swing_hh", "LH": "swing_lh", "EH": "swing_eh",
                      "HL": "swing_hl", "LL": "swing_ll", "EL": "swing_el"}


def catalog() -> List[Tuple[str, str, str]]:
    """(code, kind, description) rows for PATTERN_CATALOG."""
    rows = []
    for name in LINES:
        lab = LINE_LABELS[name]
        rows += [
            (f"{name}_test_from_above", "line", f"Price came down to the {lab}: a test starts"),
            (f"{name}_test_from_below", "line", f"Price came up to the {lab}: a test starts"),
            (f"{name}_bounce_up", "line", f"The test of the {lab} ended back above it (the line held as support)"),
            (f"{name}_bounce_down", "line", f"The test of the {lab} ended back below it (the line held as resistance)"),
            (f"{name}_break_up", "line", f"The test of the {lab} ended with a close above it, coming from below (broke through)"),
            (f"{name}_break_down", "line", f"The test of the {lab} ended with a close below it, coming from above (broke through)"),
        ]
    rows += [
        ("day_high_break", "level", "A new high for the day so far"),
        ("day_low_break", "level", "A new low for the day so far"),
        ("prev_day_high_break", "level", "Close crossed above the previous day's high"),
        ("prev_day_low_break", "level", "Close crossed below the previous day's low"),
        ("swing_hh", "structure", "Swing high above the previous swing high (Higher High), with leg % and swing ratio"),
        ("swing_lh", "structure", "Swing high below the previous swing high (Lower High), with leg % and swing ratio"),
        ("swing_eh", "structure", "Swing high level with the previous swing high (within 0.05%)"),
        ("swing_hl", "structure", "Swing low above the previous swing low (Higher Low), with leg % and swing ratio"),
        ("swing_ll", "structure", "Swing low below the previous swing low (Lower Low), with leg % and swing ratio"),
        ("swing_el", "structure", "Swing low level with the previous swing low (within 0.05%)"),
    ]
    return rows


ALL_EVENT_NAMES = frozenset(code for code, _, _ in catalog())
