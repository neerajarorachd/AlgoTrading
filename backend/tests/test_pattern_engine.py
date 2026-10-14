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
from pattern_engine import PatternEvaluator
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
