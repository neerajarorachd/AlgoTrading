"""Point events added in market story P1 (plan: docs/market_story_plan.md).

Pure functions; ActivityEngine calls them per closed candle when the engine
setting `point_events_v2` is on (off by default).

Line events, for VWAP, BB upper/middle/lower, MA21, MA50 and EMA50; at most
one per line per candle, in this order:
- <line>_cross_up / _cross_down: the close moved to the other side of the line;
- <line>_bounce_up: price was above, the low came within touch % of the line
  (or below it) and the close is back above: the line held as support;
- <line>_bounce_down: the mirror, the line held as resistance;
- <line>_touch: the candle reached the line without any of the above.
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
DEFAULT_TOUCH_PCT = 0.05
DEFAULT_CLEAR_PCT = 0.10  # same as the story's "hugging" band

Event = Tuple[str, str, Optional[float]]  # (activity_type, activity, intensity)


def _dist(close: float, level: float) -> float:
    return abs(close - level) / level * 100 if level else 0.0


def line_events(prev: Optional[dict], row: dict, touch_pct: float = DEFAULT_TOUCH_PCT,
                clear_pct: float = DEFAULT_CLEAR_PCT) -> List[Event]:
    """prev / row: frame rows (close, high, low and the line fields) for the
    previous and the current candle of one instrument + timeframe.
    A bounce needs both closes (before and after) at least clear_pct % away
    from the line, so price hovering around a line counts as touches, not
    as a stream of bounces."""
    out: List[Event] = []
    if prev is None:
        return out
    c, h, l = row["close"], row["high"], row["low"]
    pc = prev.get("close")
    for name in LINES:
        line, pline = row.get(name), prev.get(name)
        if line is None or pline is None or pc is None:
            continue
        tol = line * touch_pct / 100
        was_above, was_below = pc > pline, pc < pline
        clear_before = _dist(pc, pline) >= clear_pct
        clear_now = _dist(c, line) >= clear_pct
        reached_from_above = l <= line + tol
        reached_from_below = h >= line - tol
        if was_below and c > line:
            ev = "cross_up"
        elif was_above and c < line:
            ev = "cross_down"
        elif was_above and clear_before and clear_now and reached_from_above and c > line:
            ev = "bounce_up"
        elif was_below and clear_before and clear_now and reached_from_below and c < line:
            ev = "bounce_down"
        elif l - tol <= line <= h + tol:
            ev = "touch"
        else:
            continue
        out.append(("line", f"{name}_{ev}", _dist(c, line)))
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
            (f"{name}_cross_up", "line", f"Close crossed above the {lab}"),
            (f"{name}_cross_down", "line", f"Close crossed below the {lab}"),
            (f"{name}_bounce_up", "line", f"Price came down to the {lab} and closed back above it (the line held as support)"),
            (f"{name}_bounce_down", "line", f"Price came up to the {lab} and closed back below it (the line held as resistance)"),
            (f"{name}_touch", "line", f"The candle reached the {lab} without crossing or bouncing"),
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
