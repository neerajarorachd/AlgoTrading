"""2026-10-30: ADX / +DI / -DI, the TIME and TF_MINUTES condition words, the
three trend-filtered system patterns, and pattern_backtest_results
(memory: cross_widening_signal)."""
import ast
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from activity_engine import ActivityEngine
from brokers.models import Candle
from condition_evaluator import Field, _eval_node, _normalize
from db.models import CandleIndicators, Pattern, PatternBacktestResult
from db.ops import LibPatternBacktests, LibPatterns
from indicators import AdxState, update_adx
from pattern_engine import PatternEvaluator
from patterns_catalog import SYSTEM_PATTERNS

NEW = ("macd_widening_trend_bull", "macd_widening_trend_bear", "ma_death_cross_trend")


def _ev(formula, df, context=None):
    return _eval_node(ast.parse(_normalize(formula), mode="eval"), df, context or {})


# ---- ADX ----

def test_adx_warms_up_then_reads_a_steady_uptrend():
    st = AdxState()
    out = [update_adx(st, 100 + i + 1, 100 + i - 1, 100 + i + 0.5) for i in range(40)]
    assert out[0] == (None, None, None)
    assert out[13][1] is None and out[14][1] is not None  # DI after 14 candle-to-candle moves
    assert out[26][0] is None and out[27][0] is not None  # ADX after ~2 x 14
    adx, plus_di, minus_di = out[-1]
    assert plus_di > minus_di and adx > 50


def test_adx_reads_a_downtrend_as_minus_di():
    st = AdxState()
    out = [update_adx(st, 200 - i + 1, 200 - i - 1, 200 - i - 0.5) for i in range(40)]
    adx, plus_di, minus_di = out[-1]
    assert minus_di > plus_di and adx > 50


def test_engine_rows_carry_adx(session_factory):
    class E(ActivityEngine):
        def _lookup_instrument_id(self, s, seg):
            return 1
    e = E(session_factory, swing_lookback=2)
    t0 = datetime(2026, 10, 29, 3, 45, tzinfo=timezone.utc)
    for i in range(40):
        p = 100 + i
        e.on_candle_closed("X", "NSE_EQ", Candle("X", "5min", t0 + timedelta(minutes=5 * i), p, p + 1, p - 1, p + 0.5, 1000))
    row = e.recent_rows("X", "NSE_EQ", "5min", 1)[0]
    assert row["adx"] is not None and row["plus_di"] > row["minus_di"]
    assert {"adx", "plus_di", "minus_di"} <= set(e._indicator_buffer[-1])
    CandleIndicators(**e._indicator_buffer[-1])  # the stored row accepts the new columns


def test_adx_fields_in_the_condition_language():
    assert {Field.ADX.value, Field.PLUS_DI.value, Field.MINUS_DI.value} == {"adx", "plus_di", "minus_di"}
    LibPatterns.validate({"key": "k", "name": "n", "pattern_type": "bullish",
                          "conditions": ["ADX >= 30 and ADX < 40 and PLUS_DI > MINUS_DI"]})  # raises if invalid


# ---- TIME / TF_MINUTES ----

def _frame(minutes, start=datetime(2026, 10, 29, 4, 0, tzinfo=timezone.utc), n=3):  # 09:30 IST
    return pd.DataFrame({"ts": [start + timedelta(minutes=minutes * i) for i in range(n)], "close": [1.0] * n})


def test_time_is_ist_hhmm():
    assert _ev("TIME", _frame(5)).tolist() == [930.0, 935.0, 940.0]


def test_tf_minutes_from_spacing_or_the_frame_column():
    assert _ev("TF_MINUTES", _frame(3)).tolist() == [3.0] * 3
    df = _frame(5)
    df["tf_minutes"] = 5.0
    assert _ev("TF_MINUTES == 5 and TIME >= 930 and TIME < 1445", df).tolist() == [True] * 3


def test_time_words_without_a_ts_column_are_unknown_not_errors():
    df = pd.DataFrame({"close": [1.0, 2.0]})
    assert _ev("TIME", df).isna().all() and _ev("TF_MINUTES", df).isna().all()
    LibPatterns.validate({"key": "k", "name": "n", "pattern_type": "bullish",
                          "conditions": ["TIME >= 930 and TF_MINUTES == 5"]})  # raises if invalid


# ---- the three system patterns ----

def test_new_patterns_seed_with_four_conditions(session_factory):
    LibPatterns.seed_system_patterns(session_factory, SYSTEM_PATTERNS)
    with session_factory() as s:
        ps = {p["key"]: p for p in LibPatterns.list_patterns(s)}
    for key in NEW:
        # 4 conditions: the 80% formed rule then needs all four, as backtested
        assert len(ps[key]["conditions"]) == 4
        assert ps[key]["origin"] == "system" and ps[key]["cooldown"] == 0
    assert ps["macd_widening_trend_bull"]["pattern_type"] == "bullish"
    assert ps["macd_widening_trend_bear"]["pattern_type"] == "bearish"


def _rows(n, start, step, close_fn, adx, plus_di, minus_di, events_at):
    rows = []
    for i in range(n):
        c = close_fn(i)
        rows.append({"ts": start + timedelta(minutes=step * i), "open": c, "high": c + 1, "low": c - 1, "close": c,
                     "volume": 1000, "ema50": c - 5 if adx else None, "adx": adx, "plus_di": plus_di,
                     "minus_di": minus_di, "_events": set(events_at.get(i, ()))})
    return rows


@pytest.mark.parametrize("step, hhmm_start, adx, expect", [
    (5, (4, 0), 35, True),     # 5-min, 09:30 IST, ADX 35: all four hold
    (5, (4, 0), 45, False),    # ADX 45: outside 30-40
    (3, (4, 0), 35, False),    # 3-min: this pattern is 5-min only
    (5, (9, 30), 35, False),   # 15:00 IST: after the 14:45 entry cut-off
])
def test_bull_pattern_forms_only_when_all_four_hold(session_factory, step, hhmm_start, adx, expect):
    LibPatterns.seed_system_patterns(session_factory, SYSTEM_PATTERNS)
    ev = PatternEvaluator(session_factory, owner_id=None)
    start = datetime(2026, 10, 29, *hhmm_start, tzinfo=timezone.utc) - timedelta(minutes=step * 14)
    rows = _rows(15, start, step, lambda i: 100 + i, adx, 30, 15, {14: ["macd_bullish_cross_widening"]})
    hits = []
    for k in range(1, len(rows) + 1):
        hits += ev.on_candle(("X", "NSE_EQ", f"{step}min"), rows[:k], f"{step}min")
    keys = {h["key"] for h in hits}
    assert ("macd_widening_trend_bull" in keys) is expect


# ---- pattern_backtest_results ----

def _result(**over):
    row = {"run_label": "t", "pattern_key": "macd_widening_trend_bull", "combination": "a AND b",
           "timeframe": "5min", "scope": "set", "stock_set": "BHEL,CUB", "exit_rule": "stop 2xATR",
           "period_start": "2024-09-17", "period_end": "2026-09-16", "capital": 500000, "leg_size": 250000,
           "cost_model": "itemized", "trades": 100, "win_rate": 0.45, "avg_net_pct": 0.12, "net_rupees": 1000.5}
    row.update(over)
    return row


def test_save_run_stores_rows_and_copies_set_stats_onto_the_pattern(session_factory):
    LibPatterns.seed_system_patterns(session_factory, SYSTEM_PATTERNS)
    n = LibPatternBacktests.save_run(session_factory, [
        _result(), _result(scope="stock", symbol="BHEL", trades=40, win_rate=0.9)])
    assert n == 2
    with session_factory() as s:
        p = s.query(Pattern).filter_by(key="macd_widening_trend_bull", owner_id=None).one()
        assert p.sample_size == 100 and float(p.win_rate) == 0.45 and float(p.avg_move_pct) == 0.12
        assert p.last_backtested_at is not None
        rows = LibPatternBacktests.list_results(s, pattern_key="macd_widening_trend_bull")
        assert len(rows) == 2 and all(r["pattern_id"] == p.id for r in rows)
        assert {r["symbol"] for r in rows} == {None, "BHEL"} and rows[0]["net_rupees"] in (1000.5, None)
        assert s.query(PatternBacktestResult).filter_by(scope="stock").one().period_start.isoformat() == "2024-09-17"


@pytest.mark.parametrize("bad", [{"scope": "all"}, {"scope": "stock"}, {"trades": None}])
def test_save_run_rejects_bad_rows(session_factory, bad):
    with pytest.raises(LibPatternBacktests.BacktestResultError):
        LibPatternBacktests.save_run(session_factory, [_result(**bad)])
