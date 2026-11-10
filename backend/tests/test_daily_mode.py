"""Daily mode (2026-11-10): one candle = one trading day, for long-term
analysis. Pattern state never resets by day, a condition sees the last N
candles (not just today + the previous session), and the engine adds EMA200,
MA200 and weekly RSI14 while leaving VWAP out."""
from datetime import date, datetime, timedelta, timezone

import pytest

from activity_engine import ActivityEngine
from brokers.models import Candle
from condition_evaluator import frame_from_rows, parse_and_evaluate, required_lookback, timeframe_minutes
from db.models import SubscribedSymbol
from db.ops import LibPatterns
from db.ops.LibSystemSettings import seed_defaults as seed_settings
from indicators import RsiState, update_rsi
from pattern_engine import PatternEvaluator
from patterns_catalog import PATTERN_SETTING_SEED

IST = timedelta(hours=5, minutes=30)
KEY = ("X", "NSE_EQ", "1day")


def _day_ts(d: date) -> datetime:
    """Dhan stamps a daily candle at IST midnight of its day."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) - IST


def _weekdays(n, start=date(2025, 1, 6)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _rows(rsis):
    return [{"ts": _day_ts(d), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100,
             "rsi": r, "_events": set()} for d, r in zip(_weekdays(len(rsis)), rsis)]


@pytest.fixture
def factory(session_factory):
    seed_settings(session_factory, PATTERN_SETTING_SEED)
    with session_factory() as s:
        s.add(SubscribedSymbol(symbol="X", exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
                               security_id="1", previous_close=100))
        s.commit()
    return session_factory


def _pattern(factory, key, conditions, cooldown=0):
    LibPatterns.create_pattern(factory, {"key": key, "name": key, "pattern_type": "bullish", "manual_weight": 4,
                                         "cooldown": cooldown, "conditions": list(conditions)},
                               owner_id=None, origin="system")


def test_timeframe_minutes_of_a_day():
    assert timeframe_minutes("1day") == 1440 and timeframe_minutes("5min") == 5


def test_daily_window_spans_many_days(factory):
    # RSI > 90 seven trading days ago: intraday mode only reads 2 sessions (= 2 daily
    # candles) and misses it; daily mode reads the last 10 candles
    _pattern(factory, "recent_high", ["RECENTLY(RSI > 90, 10)"])
    rows = _rows([95, 40, 40, 40, 40, 40, 40, 40])
    daily, intraday = PatternEvaluator(factory, daily=True), PatternEvaluator(factory)
    assert [h["key"] for h in daily.on_candle(KEY, rows, "1day")] == ["recent_high"]
    assert intraday.on_candle(KEY, rows, "1day") == []


def test_daily_state_does_not_reset_each_day(factory):
    # formed on day 0 and still formed: one hit only (intraday mode would reset
    # every "day" = every daily candle and hit again each time)
    _pattern(factory, "rsi_high", ["RSI > 60"])
    rows = _rows([65, 70, 72, 75])
    ev = PatternEvaluator(factory, daily=True)
    hits = [[h["key"] for h in ev.on_candle(KEY, rows[: i + 1], "1day")] for i in range(len(rows))]
    assert hits == [["rsi_high"], [], [], []]


def test_daily_cooldown_counts_candles_across_days(factory):
    _pattern(factory, "rsi_high", ["RSI > 60"], cooldown=3)
    rows = _rows([65, 50, 65, 50, 50, 65])
    ev = PatternEvaluator(factory, daily=True)
    hits = [[h["key"] for h in ev.on_candle(KEY, rows[: i + 1], "1day")] for i in range(len(rows))]
    # re-forms on candle 2 inside the 3-candle cooldown (suppressed), again on 5 (allowed)
    assert hits == [["rsi_high"], [], [], [], [], ["rsi_high"]]


def test_daily_batch_matches_candle_by_candle(factory):
    _pattern(factory, "recent_high", ["RECENTLY(RSI > 90, 10)", "RSI < 50"])
    _pattern(factory, "rising", ["RSI > RSI[-1]"])
    rsis = [40, 95, 42, 44, 41, 43, 92, 40, 41, 45, 44, 46, 47, 30, 31]
    rows = _rows(rsis)
    ev1, ev2 = PatternEvaluator(factory, daily=True), PatternEvaluator(factory, daily=True)
    expected = [[h["key"] for h in ev1.on_candle(KEY, rows[: i + 1], "1day")] for i in range(len(rows))]
    out = ev2.evaluate_batch(KEY, rows, [(i, True) for i in range(len(rows))], "1day")
    assert [[h["key"] for h in out.get(i, [])] for i in range(len(rows))] == expected
    assert ev2._state == ev1._state and ev2.scores == ev1.scores


def _daily_candles(n):
    out, price = [], 100.0
    for i, d in enumerate(_weekdays(n)):
        o = price
        price = price * (1.004 if i % 3 else 0.995)
        out.append(Candle(symbol="X", timeframe="1day", timestamp=_day_ts(d), open=o, high=max(o, price) * 1.002,
                          low=min(o, price) * 0.998, close=price, volume=1000 + i))
    return out


def test_engine_daily_long_term_fields(factory):
    engine = ActivityEngine(factory, daily=True)
    candles = _daily_candles(260)
    for c in candles:
        engine.on_candle_closed("X", "NSE_EQ", c)
    rows = engine.recent_rows("X", "NSE_EQ", "1day", 400)
    assert len(rows) == 260  # more than the intraday 120
    assert rows[198]["ma200"] is None and rows[199]["ma200"] == pytest.approx(sum(c.close for c in candles[:200]) / 200)
    assert rows[-1]["ema200"] is not None and all(r["vwap"] is None for r in rows)
    # weekly RSI: finished weeks' last closes, then this week's close so far
    weekly, week, last = [], None, None
    for c in candles:
        wk = (c.timestamp + IST).date().isocalendar()[:2]
        if week is not None and wk != week:
            weekly.append(last)
        week, last = wk, c.close
    st = RsiState()
    value = None
    for close in weekly + [last]:
        value = update_rsi(st, close)
    assert rows[-1]["rsi_weekly"] == pytest.approx(value)


def test_intraday_engine_has_no_long_term_fields(factory):
    engine = ActivityEngine(factory)
    for c in _daily_candles(5):
        engine.on_candle_closed("X", "NSE_EQ", c)
    assert "ema200" not in engine.recent_rows("X", "NSE_EQ", "1day", 1)[-1]


def test_52_week_high_reaches_250_candles():
    assert required_lookback("CLOSE >= HIGH_52W") == 250
    assert required_lookback("CLOSE > HIGH_52W[-1]") == 251
    rows = [{"ts": _day_ts(d), "high": float(i), "low": 0.0, "close": float(i)} for i, d in enumerate(_weekdays(300))]
    df = frame_from_rows(rows)
    high = parse_and_evaluate("HIGH_52W", df)
    assert pd_isna(high.iloc[248]) and high.iloc[249] == 249 and high.iloc[-1] == 299


def pd_isna(v):
    import pandas as pd
    return pd.isna(v)
