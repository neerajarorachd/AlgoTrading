"""ALGO-32: live pattern evaluation -- match %, the 80% formed rule, one hit
per formation (edge-triggered), cooldown, day reset, and the engine wiring
(hits recorded as activities, warm-up advances state without recording)."""
from datetime import datetime, timedelta, timezone

import pytest

from activity_engine import ActivityEngine
from brokers.models import Candle
from db.models import SubscribedSymbol
from db.ops import LibPatterns
from db.ops.LibSystemSettings import seed_defaults as seed_settings
from pattern_engine import LATE_EVENTS, PatternEvaluator
from patterns_catalog import PATTERN_SETTING_SEED

OPEN = datetime(2026, 10, 13, 3, 45, tzinfo=timezone.utc)  # 09:15 IST


def _pattern(factory, key="rsi_high", conditions=("RSI > 60",), weight=4, cooldown=0, ptype="bullish"):
    return LibPatterns.create_pattern(factory, {
        "key": key, "name": key, "pattern_type": ptype, "manual_weight": weight,
        "cooldown": cooldown, "conditions": list(conditions)}, owner_id=None, origin="system")


def _rows(rsis, start=OPEN, events=None):
    rows = []
    for i, rsi in enumerate(rsis):
        rows.append({"ts": start + timedelta(minutes=i), "open": 100, "high": 101, "low": 99, "close": 100,
                     "volume": 100, "rsi": rsi, "_events": set((events or {}).get(i, ()))})
    return rows


def _feed(ev, rows, key=("X", "NSE_EQ", "1min")):
    out = []
    for i in range(len(rows)):
        out.append([h["key"] for h in ev.on_candle(key, rows[: i + 1], "1min")])
    return out


@pytest.fixture
def factory(session_factory):
    seed_settings(session_factory, PATTERN_SETTING_SEED)
    return session_factory


def test_one_hit_per_formation_edge_triggered(factory):
    _pattern(factory)
    ev = PatternEvaluator(factory)
    hits = _feed(ev, _rows([50, 65, 70, 72, 55, 66]))
    # forms on candle 1, stays formed (no new hit), breaks on 4, forms again on 5
    assert hits == [[], ["rsi_high"], [], [], [], ["rsi_high"]]


def test_formed_at_80_percent_and_score_is_weight_times_match(factory):
    _pattern(factory, "five", ["RSI > 10", "RSI > 20", "RSI > 30", "RSI > 40", "RSI > 60"], weight=5)
    ev = PatternEvaluator(factory)
    key = ("X", "NSE_EQ", "1min")
    assert ev.on_candle(key, _rows([45]), "1min")[0]["score"] == pytest.approx(5 * 0.8)  # 4 of 5 = 80%: formed
    ev2 = PatternEvaluator(factory)
    assert ev2.on_candle(key, _rows([35]), "1min") == []  # 3 of 5 = 60%: not formed
    assert ev2.scores[key]["five"] == pytest.approx(0.6)  # but its match % is kept for the colours


def test_cooldown_suppresses_a_quick_reformation(factory):
    _pattern(factory, cooldown=3)
    ev = PatternEvaluator(factory)
    hits = _feed(ev, _rows([65, 50, 65, 50, 50, 50, 65]))
    # forms at 0; re-forms at 2 (only 2 candles later: suppressed); re-forms at 6 (6 later: recorded)
    assert [i for i, h in enumerate(hits) if h] == [0, 6]


def test_state_resets_at_a_new_session(factory):
    _pattern(factory)
    ev = PatternEvaluator(factory)
    key = ("X", "NSE_EQ", "1min")
    day1 = _rows([65, 70])
    for i in range(2):
        ev.on_candle(key, day1[: i + 1], "1min")
    next_open = OPEN + timedelta(days=1)
    day2 = day1 + _rows([70], start=next_open)
    assert [h["key"] for h in ev.on_candle(key, day2, "1min")] == ["rsi_high"]  # formed again: new day, new hit


def test_event_condition_reads_the_candles_events(factory):
    _pattern(factory, "hammer_seen", ["EVENT_ANY(hammer)"])
    ev = PatternEvaluator(factory)
    hits = _feed(ev, _rows([50, 50, 50], events={1: {"hammer"}}))
    assert hits == [[], ["hammer_seen"], []]


def test_disabled_patterns_are_not_evaluated(factory):
    pid = _pattern(factory)
    LibPatterns.update_pattern(factory, pid, {"enabled": False})
    assert PatternEvaluator(factory).patterns == []


# ---- engine wiring ----

def _candles(n, start=OPEN, step=0.3):
    out, price = [], 100.0
    for i in range(n):
        o = price
        price = price + (step if i % 2 == 0 else -step / 2)
        out.append(Candle(symbol="X", timeframe="1min", timestamp=start + timedelta(minutes=i),
                          open=o, high=max(o, price) + 0.05, low=min(o, price) - 0.05, close=price, volume=1000))
    return out


def _register(factory):
    with factory() as s:
        s.add(SubscribedSymbol(symbol="X", exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ", security_id="1", previous_close=100))
        s.commit()


def test_engine_records_pattern_hits_as_activities(factory):
    _register(factory)
    _pattern(factory, "always_up", ["CLOSE > 0"])
    engine = ActivityEngine(factory)
    engine.pattern_evaluator = PatternEvaluator(factory)
    for c in _candles(5):
        engine.on_candle_closed("X", "NSE_EQ", c)
    hits = [a for a in engine._buffer if a["activity_type"] == "pattern"]
    assert len(hits) == 1 and hits[0]["activity"] == "always_up" and hits[0]["ts"] == OPEN
    assert hits[0]["intensity"] == pytest.approx(4.0)


def test_warm_up_advances_pattern_state_without_recording(factory):
    _register(factory)
    _pattern(factory, "always_up", ["CLOSE > 0"])
    engine = ActivityEngine(factory)
    engine.pattern_evaluator = PatternEvaluator(factory)
    candles = _candles(5)
    for c in candles[:3]:
        engine.warm_up("X", "NSE_EQ", c, evaluate_patterns=True)
    for c in candles[3:]:
        engine.on_candle_closed("X", "NSE_EQ", c)
    # it formed during the warmed part, so nothing new is recorded afterwards
    assert [a for a in engine._buffer if a["activity_type"] == "pattern"] == []


def test_engine_without_an_evaluator_is_unchanged(factory):
    _register(factory)
    engine = ActivityEngine(factory)
    for c in _candles(5):
        engine.on_candle_closed("X", "NSE_EQ", c)
    assert all(a["activity_type"] != "pattern" for a in engine._buffer)


def test_a_hit_keeps_each_conditions_numbers(factory):
    # hit review (2026-10-16): why it formed -- required and actual per condition
    _pattern(factory, "five", ["RSI > 10", "RSI > 20", "RSI > 30", "RSI > 40", "RSI > 60"], weight=5)
    ev = PatternEvaluator(factory)
    key = ("X", "NSE_EQ", "1min")
    ev.on_candle(key, _rows([45]), "1min")
    (hit,) = ev.explanations[key]
    assert (hit["pattern"], hit["met"], hit["of"]) == ("five", 4, 5)
    last = hit["conditions"][-1]
    assert (last["formula"], last["required"], last["actual"], last["met"]) == ("RSI > 60", "> 60.0", 45.0, False)


def test_running_data_reaches_into_the_previous_session(factory):
    # 2026-10-16: the market-open candle reads yesterday's last one (RSI[-1])
    _pattern(factory, "rising", ["RSI > RSI[-1]"])
    ev = PatternEvaluator(factory)
    key = ("X", "NSE_EQ", "1min")
    yesterday = _rows([40, 50], start=OPEN - timedelta(days=1))
    today = _rows([55], start=OPEN)
    assert [h["key"] for h in ev.on_candle(key, yesterday + today, "1min")] == ["rising"]  # 55 > yesterday's 50


def test_only_two_sessions_are_read(factory):
    # three sessions back is not "running data": the window is today + the previous session
    _pattern(factory, "recent_high", ["RECENTLY(RSI > 90, 10)"])
    ev = PatternEvaluator(factory)
    key = ("X", "NSE_EQ", "1min")
    rows = _rows([95], start=OPEN - timedelta(days=2)) + _rows([40], start=OPEN - timedelta(days=1)) + _rows([40], start=OPEN)
    assert ev.on_candle(key, rows, "1min") == []


# ---- batch mode (2026-11-10): same result as candle by candle ----

def _candle_by_candle(ev, key, snapshots):
    return [[h["key"] for h in ev.on_candle(key, snap, "1min")] for snap in snapshots]


def _batch(ev, key, rows):
    out = ev.evaluate_batch(key, rows, [(i, True) for i in range(len(rows))], "1min")
    return [[h["key"] for h in out.get(i, [])] for i in range(len(rows))]


def test_batch_does_not_see_an_event_before_it_was_written(factory):
    # a swing lands on its candle 5 candles later: candle by candle, row 2's
    # swing_low only exists from candle 7 on -- the batch must agree
    _pattern(factory, "after_swing", ["RECENTLY(EVENT_ANY(swing_low), 6)"])
    key = ("X", "NSE_EQ", "1min")
    rows = _rows([50] * 12)
    rows[2]["_events"] = {"swing_low"}
    rows[2][LATE_EVENTS] = {"swing_low": rows[7]["ts"]}
    snapshots = []
    for i in range(12):
        snap = list(rows[: i + 1])
        if 2 <= i < 7:
            snap[2] = {**snap[2], "_events": set()}
        snapshots.append(snap)
    ev1, ev2 = PatternEvaluator(factory), PatternEvaluator(factory)
    expected = _candle_by_candle(ev1, key, snapshots)
    assert expected[7] == ["after_swing"]
    assert _batch(ev2, key, rows) == expected
    assert ev2.scores == ev1.scores and ev2._state == ev1._state and ev2.explanations == ev1.explanations


def test_batch_reads_the_same_two_sessions_window(factory):
    _pattern(factory, "recent_high", ["RECENTLY(RSI > 90, 10)"])
    _pattern(factory, "rising", ["RSI > RSI[-1]", "RSI > 45"])
    key = ("X", "NSE_EQ", "1min")
    rows = (_rows([95, 40, 41], start=OPEN - timedelta(days=2)) + _rows([40, 92, 41], start=OPEN - timedelta(days=1))
            + _rows([40, 50, 60, 55, 70, 30, 95, 96], start=OPEN))
    ev1, ev2 = PatternEvaluator(factory), PatternEvaluator(factory)
    expected = _candle_by_candle(ev1, key, [rows[: i + 1] for i in range(len(rows))])
    assert _batch(ev2, key, rows) == expected
    assert ev2.scores == ev1.scores and ev2._state == ev1._state and ev2.explanations == ev1.explanations


def _engine_hits(factory, deferred, candles):
    engine = ActivityEngine(factory)
    engine.pattern_evaluator = PatternEvaluator(factory)
    if deferred:
        assert engine.begin_deferred_patterns("X", "NSE_EQ")
    for n, c in enumerate(candles):
        if n < len(candles) // 2:
            engine.warm_up("X", "NSE_EQ", c, evaluate_patterns=True)
        else:
            engine.on_candle_closed("X", "NSE_EQ", c)
    if deferred:
        engine.end_deferred_patterns("X", "NSE_EQ")
    assert engine._deferred == {}
    pats = sorted((a["ts"], a["activity"], a["intensity"]) for a in engine._buffer if a["activity_type"] == "pattern")
    return pats, engine


def test_engine_deferred_patterns_match_candle_by_candle(factory):
    _register(factory)
    _pattern(factory, "up", ["CLOSE > CLOSE[-1]"])
    _pattern(factory, "down", ["CLOSE < CLOSE[-1]"])
    _pattern(factory, "swing", ["RECENTLY(EVENT_ANY(swing_high), 3)"])
    candles = _candles(60, step=0.6)
    plain, e1 = _engine_hits(factory, False, candles)
    batch, e2 = _engine_hits(factory, True, candles)
    assert plain and batch == plain
    assert e2.pattern_evaluator._state == e1.pattern_evaluator._state
    assert e2.signal_explanations == e1.signal_explanations


def test_batch_eval_setting_off_keeps_candle_by_candle(factory):
    from db.ops.LibSystemSettings import set_value
    set_value(factory, "pattern_batch_eval", 0)
    engine = ActivityEngine(factory)
    engine.pattern_evaluator = PatternEvaluator(factory)
    assert engine.begin_deferred_patterns("X", "NSE_EQ") is False


def test_explain_off_keeps_the_same_hits_without_notes(factory):
    _register(factory)
    _pattern(factory, "up", ["CLOSE > CLOSE[-1]"])
    _pattern(factory, "down", ["CLOSE < CLOSE[-1]"])
    candles = _candles(40, step=0.6)
    on, e1 = _engine_hits(factory, True, candles)
    engine = ActivityEngine(factory)
    engine.pattern_evaluator = PatternEvaluator(factory, explain=False)
    engine.explain_signals = False
    engine.begin_deferred_patterns("X", "NSE_EQ")
    for n, c in enumerate(candles):
        if n < len(candles) // 2:
            engine.warm_up("X", "NSE_EQ", c, evaluate_patterns=True)
        else:
            engine.on_candle_closed("X", "NSE_EQ", c)
    engine.end_deferred_patterns("X", "NSE_EQ")
    off = sorted((a["ts"], a["activity"], a["intensity"]) for a in engine._buffer if a["activity_type"] == "pattern")
    assert on and off == on
    assert any(e1.pattern_evaluator.explanations.values())
    assert not any(engine.pattern_evaluator.explanations.values()) and not any(engine.signal_explanations.values())
