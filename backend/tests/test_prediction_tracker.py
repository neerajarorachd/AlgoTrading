from datetime import datetime, timedelta, timezone

import pytest

from activity_engine import ActivityEngine
from brokers.models import Candle
from db.models import CandleToday, PatternPrediction, SubscribedSymbol
from prediction_tracker import (
    OUTCOME_SCAN_CANDLES,
    PredictionTracker,
    crossover_stop_loss,
    crossover_target,
)

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"
_BASE_TS = datetime(2026, 9, 11, 9, 15, tzinfo=timezone.utc)


def _candle(minute, open, high, low, close, timeframe="1min"):
    return Candle(
        symbol=SYMBOL, timeframe=timeframe, timestamp=_BASE_TS + timedelta(minutes=minute),
        open=open, high=high, low=low, close=close, volume=100,
    )


def _register_symbol(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()


def _make_prediction(session_factory, **overrides):
    """Inserts an already-persisted PatternPrediction row directly,
    bypassing PredictionTracker entirely — callers must construct the
    PredictionTracker AFTER calling this, so its constructor's
    _load_existing() picks the row up into memory with a real id
    (mirroring how a live process resumes tracking on restart)."""
    kwargs = dict(
        instrument_id=1, timeframe="3min", pattern="rsi_cross_above_60", direction="bull",
        detected_ts=_BASE_TS, entry_price=100.0, neckline=None,
        stop_loss=98.0, target=104.0, candles_checked=0,
    )
    kwargs.update(overrides)
    with session_factory() as session:
        pred = PatternPrediction(**kwargs)
        session.add(pred)
        session.commit()
        return pred.id


# --------------------------------------------------------------------- pure functions

def test_crossover_stop_loss_and_target_for_a_bullish_call():
    stop = crossover_stop_loss(entry=100.0, atr=2.0, direction="bull", atr_multiplier=1.5)
    target = crossover_target(entry=100.0, atr=2.0, direction="bull", atr_multiplier=1.5, risk_reward_ratio=2.0)
    assert stop == 97.0  # 100 - 1.5*2
    assert target == 106.0  # 100 + 1.5*2*2


def test_crossover_stop_loss_and_target_for_a_bearish_call():
    stop = crossover_stop_loss(entry=100.0, atr=2.0, direction="bear", atr_multiplier=1.5)
    target = crossover_target(entry=100.0, atr=2.0, direction="bear", atr_multiplier=1.5, risk_reward_ratio=2.0)
    assert stop == 103.0
    assert target == 94.0


# --------------------------------------------------------------------- on_activities
#
# on_activities buffers new predictions in memory (self._unpersisted) —
# they only reach the DB once flush() is called (automatically roughly
# once a minute, or explicitly). Every test that asserts against the DB
# calls tracker.flush() first.

def test_on_activities_opens_an_atr_based_prediction_for_a_tracked_crossover(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    # Feed 15 quiet candles (ATR_PERIOD=14) purely to seed ATR — no pattern
    # needs to actually fire for this test, on_activities is driven by a
    # hand-built activity dict below (isolating it from the detectors).
    for i in range(15):
        engine.on_candle_closed(SYMBOL, SEG, _candle(i, 100.0, 101.0, 99.0, 100.0, timeframe="3min"))
    atr = engine.get_atr(SYMBOL, SEG, "3min")
    assert atr is not None

    tracker = PredictionTracker(session_factory, engine)
    activity = {
        "instrument_id": 1, "timeframe": "3min", "ts": _BASE_TS,
        "activity_type": "indicator", "activity": "rsi_cross_above_60", "intensity": 1.0,
        "open_price": 100.0, "high_price": 101.0, "low_price": 99.0, "close_price": 100.0,
    }
    opened = tracker.on_activities(SYMBOL, SEG, [activity])
    assert opened == 1
    assert len(tracker._pending[(1, "3min")]) == 1  # tracked immediately, before any flush
    assert len(tracker._unpersisted) == 1

    with session_factory() as session:
        assert session.query(PatternPrediction).count() == 0  # not flushed yet

    inserted, updated = tracker.flush()
    assert (inserted, updated) == (1, 0)
    assert tracker._unpersisted == []

    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.pattern == "rsi_cross_above_60"
    assert pred.direction == "bull"
    assert pred.neckline is None
    assert float(pred.stop_loss) == pytest.approx(crossover_stop_loss(100.0, atr, "bull", tracker.atr_multiplier))
    assert float(pred.target) == pytest.approx(crossover_target(100.0, atr, "bull", tracker.atr_multiplier, tracker.risk_reward_ratio))


def test_on_activities_tracks_the_four_directional_channel_patterns(session_factory):
    """ascending_triangle/falling_wedge are bullish, descending_triangle/
    rising_wedge are bearish — symmetrical_triangle/rectangle are
    deliberately NOT in this set (no directional bias without a
    confirmed breakout), so only these four should open a prediction."""
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    for i in range(15):
        engine.on_candle_closed(SYMBOL, SEG, _candle(i, 100.0, 101.0, 99.0, 100.0, timeframe="3min"))
    tracker = PredictionTracker(session_factory, engine)

    def _activity(pattern):
        return {
            "instrument_id": 1, "timeframe": "3min", "ts": _BASE_TS,
            "activity_type": "graph_formation", "activity": pattern, "intensity": 1.0,
            "open_price": 100.0, "high_price": 101.0, "low_price": 99.0, "close_price": 100.0,
        }

    assert tracker.on_activities(SYMBOL, SEG, [_activity("ascending_triangle")]) == 1
    assert tracker.on_activities(SYMBOL, SEG, [_activity("falling_wedge")]) == 1
    assert tracker.on_activities(SYMBOL, SEG, [_activity("descending_triangle")]) == 1
    assert tracker.on_activities(SYMBOL, SEG, [_activity("rising_wedge")]) == 1
    assert tracker.on_activities(SYMBOL, SEG, [_activity("symmetrical_triangle")]) == 0
    assert tracker.on_activities(SYMBOL, SEG, [_activity("rectangle")]) == 0


def test_on_activities_tracks_directional_candlestick_patterns_via_the_generic_atr_branch(session_factory):
    """hammer/bullish_engulfing/etc. have no measured-move geometry of
    their own (unlike double_top/bottom) — they should fall through to
    the same generic ATR-based stop/target branch a crossover uses.
    doji is deliberately NOT tracked (pure indecision, no directional
    bias)."""
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    for i in range(15):
        engine.on_candle_closed(SYMBOL, SEG, _candle(i, 100.0, 101.0, 99.0, 100.0, timeframe="3min"))
    tracker = PredictionTracker(session_factory, engine)

    def _activity(pattern, kind="single_candle"):
        return {
            "instrument_id": 1, "timeframe": "3min", "ts": _BASE_TS,
            "activity_type": kind, "activity": pattern, "intensity": 1.0,
            "open_price": 100.0, "high_price": 101.0, "low_price": 99.0, "close_price": 100.0,
        }

    assert tracker.on_activities(SYMBOL, SEG, [_activity("hammer")]) == 1
    assert tracker.on_activities(SYMBOL, SEG, [_activity("bullish_engulfing", "multi_candle")]) == 1
    assert tracker.on_activities(SYMBOL, SEG, [_activity("shooting_star")]) == 1
    assert tracker.on_activities(SYMBOL, SEG, [_activity("evening_star", "multi_candle")]) == 1
    assert tracker.on_activities(SYMBOL, SEG, [_activity("doji")]) == 0  # no directional bias

    tracker.flush()
    with session_factory() as session:
        hammer = session.query(PatternPrediction).filter_by(pattern="hammer").one()
        star = session.query(PatternPrediction).filter_by(pattern="shooting_star").one()
    assert hammer.direction == "bull"
    assert hammer.neckline is None  # ATR-based, not a measured-move formation
    assert star.direction == "bear"


def test_on_activities_skips_a_crossover_when_atr_is_not_ready_yet(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)  # no candles fed at all — ATR is None
    tracker = PredictionTracker(session_factory, engine)
    activity = {
        "instrument_id": 1, "timeframe": "3min", "ts": _BASE_TS,
        "activity_type": "indicator", "activity": "macd_bullish_cross", "intensity": 1.0,
        "open_price": 100.0, "high_price": 101.0, "low_price": 99.0, "close_price": 100.0,
    }
    assert tracker.on_activities(SYMBOL, SEG, [activity]) == 0
    tracker.flush()
    with session_factory() as session:
        assert session.query(PatternPrediction).count() == 0


def test_on_activities_ignores_untracked_activity_types(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    activity = {
        "instrument_id": 1, "timeframe": "1min", "ts": _BASE_TS,
        "activity_type": "candle_pattern", "activity": "doji", "intensity": None,
        "open_price": 100.0, "high_price": 101.0, "low_price": 99.0, "close_price": 100.0,
    }
    assert tracker.on_activities(SYMBOL, SEG, [activity]) == 0


def test_on_activities_never_opens_the_same_prediction_twice(session_factory):
    """Simulates a replay re-run over the same candles within one process:
    the second on_activities call for the identical activity must be a
    no-op, caught by self._known_keys in memory (no DB round trip either
    way, and no duplicate row after flush)."""
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    for i in range(15):  # seed ATR(14) — bullish_structure_shift needs it, being ATR-priced
        engine.on_candle_closed(SYMBOL, SEG, _candle(i, 100.0, 101.0, 99.0, 100.0, timeframe="1min"))
    tracker = PredictionTracker(session_factory, engine)
    activity = {
        "instrument_id": 1, "timeframe": "1min", "ts": _BASE_TS,
        "activity_type": "structure", "activity": "bullish_structure_shift", "intensity": 1.0,
        "open_price": 100.0, "high_price": 101.0, "low_price": 99.0, "close_price": 100.0,
    }
    assert tracker.on_activities(SYMBOL, SEG, [activity]) == 1
    assert tracker.on_activities(SYMBOL, SEG, [activity]) == 0

    tracker.flush()
    with session_factory() as session:
        assert session.query(PatternPrediction).count() == 1


def test_on_activities_uses_double_top_neckline_formulas(session_factory):
    from activity_engine import double_top_neckline, double_top_stop_loss, double_top_target

    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    # Same verified zigzag shape as test_activity_engine's own double-top
    # test: two comparable peaks (100.3 / 100.2) with a deep valley (95.0).
    checkpoints = [(0, 90.0), (10, 100.3), (20, 95.0), (30, 100.2), (40, 92.0)]
    highs = []
    for (i0, h0), (i1, h1) in zip(checkpoints, checkpoints[1:]):
        for j in range(i0, i1):
            highs.append(h0 + (h1 - h0) * (j - i0) / (i1 - i0))
    highs.append(checkpoints[-1][1])

    activities = []
    for i, h in enumerate(highs):
        activities.extend(engine.on_candle_closed(SYMBOL, SEG, _candle(i, h - 0.5, h, h - 1.0, h - 0.5)))
    engine.flush()

    double_top_activities = [a for a in activities if a["activity"] == "double_top"]
    assert len(double_top_activities) == 1

    points = engine.get_swing_points(SYMBOL, SEG, "1min")
    expected_neckline = double_top_neckline(points)
    expected_stop = double_top_stop_loss(points)
    expected_target = double_top_target(points)

    tracker = PredictionTracker(session_factory, engine)
    tracker.on_activities(SYMBOL, SEG, double_top_activities)
    tracker.flush()

    with session_factory() as session:
        pred = session.query(PatternPrediction).filter_by(pattern="double_top").one()
    assert float(pred.neckline) == pytest.approx(expected_neckline)
    assert float(pred.stop_loss) == pytest.approx(expected_stop)
    assert float(pred.target) == pytest.approx(expected_target)
    assert pred.direction == "bear"


# --------------------------------------------------------------------- check_pending
#
# Every test below inserts its PatternPrediction row via _make_prediction
# BEFORE constructing PredictionTracker, so the tracker's own
# _load_existing() picks it up into memory (with a real id) — check_pending
# no longer queries the DB for pending rows at all (see prediction_tracker
# module docstring), so a tracker built before the row exists would never
# see it. Because the loaded record already has a real id, a resolution
# lands in self._resolved_buffer (an UPDATE at next flush), not
# self._unpersisted — tests call tracker.flush() before asserting on the DB.

def test_check_pending_resolves_target_hit_for_a_bullish_prediction(session_factory):
    _register_symbol(session_factory)
    _make_prediction(session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)

    candle = _candle(3, open=100.0, high=105.0, low=100.0, close=104.5, timeframe="3min")
    resolved = tracker.check_pending(SYMBOL, SEG, candle)
    assert resolved == 1
    assert tracker._pending[(1, "3min")] == []  # removed from the pending list once resolved
    assert len(tracker._resolved_buffer) == 1

    inserted, updated = tracker.flush()
    assert (inserted, updated) == (0, 1)

    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "target_hit"
    assert pred.candles_checked == 1


def test_check_pending_resolves_stop_hit_for_a_bearish_prediction(session_factory):
    _register_symbol(session_factory)
    _make_prediction(session_factory, timeframe="3min", direction="bear", stop_loss=104.0, target=96.0)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)

    candle = _candle(3, open=100.0, high=105.0, low=99.0, close=104.5, timeframe="3min")
    resolved = tracker.check_pending(SYMBOL, SEG, candle)
    assert resolved == 1
    tracker.flush()

    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "stop_hit"


def test_check_pending_leaves_a_prediction_open_when_neither_level_is_hit(session_factory):
    _register_symbol(session_factory)
    _make_prediction(session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)

    candle = _candle(3, open=100.0, high=101.0, low=99.5, close=100.5, timeframe="3min")
    resolved = tracker.check_pending(SYMBOL, SEG, candle)
    assert resolved == 0

    # still pending in memory, candles_checked bumped there
    pending = tracker._pending[(1, "3min")]
    assert len(pending) == 1
    assert pending[0].candles_checked == 1
    assert tracker._resolved_buffer == []

    # nothing needed flushing — the DB row (from _make_prediction) is
    # untouched either way
    tracker.flush()
    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome is None
    assert pred.candles_checked == 0


def test_check_pending_times_out_as_sideways_after_the_scan_window(session_factory):
    _register_symbol(session_factory)
    _make_prediction(
        session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0,
        candles_checked=OUTCOME_SCAN_CANDLES - 1,
    )
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    assert tracker._pending[(1, "3min")][0].candles_checked == OUTCOME_SCAN_CANDLES - 1

    candle = _candle(3, open=100.0, high=101.0, low=99.5, close=100.5, timeframe="3min")
    resolved = tracker.check_pending(SYMBOL, SEG, candle)
    assert resolved == 1
    tracker.flush()

    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "sideways"
    assert pred.candles_checked == OUTCOME_SCAN_CANDLES


def test_check_pending_does_not_resolve_against_the_candle_it_was_detected_on(session_factory):
    _register_symbol(session_factory)
    detected_ts = _BASE_TS + timedelta(minutes=9)
    _make_prediction(
        session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0,
        detected_ts=detected_ts,
    )
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)

    # same timestamp as detected_ts — must NOT resolve against its own candle
    same_candle = Candle(
        symbol=SYMBOL, timeframe="3min", timestamp=detected_ts,
        open=100.0, high=105.0, low=99.0, close=104.5, volume=100,
    )
    resolved = tracker.check_pending(SYMBOL, SEG, same_candle)
    assert resolved == 0
    assert len(tracker._pending[(1, "3min")]) == 1


def test_check_pending_returns_zero_immediately_when_nothing_is_pending(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    candle = _candle(3, open=100.0, high=101.0, low=99.5, close=100.5, timeframe="3min")
    assert tracker.check_pending(SYMBOL, SEG, candle) == 0


def test_check_pending_resolution_before_first_flush_skips_the_update_and_inserts_final_state(session_factory):
    """A prediction opened AND resolved before flush() ever runs should
    need only one INSERT (with the final outcome already on it) — not an
    INSERT followed by a separate UPDATE."""
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    for i in range(15):
        engine.on_candle_closed(SYMBOL, SEG, _candle(i, 100.0, 101.0, 99.0, 100.0, timeframe="3min"))
    activity = {
        "instrument_id": 1, "timeframe": "3min", "ts": _BASE_TS,
        "activity_type": "indicator", "activity": "rsi_cross_above_60", "intensity": 1.0,
        "open_price": 100.0, "high_price": 100.0, "low_price": 100.0, "close_price": 100.0,
    }
    tracker.on_activities(SYMBOL, SEG, [activity])
    assert len(tracker._unpersisted) == 1
    # overwrite the ATR-derived levels with round numbers — isolates this
    # test from the crossover formula itself (covered elsewhere) so the
    # candle below can deterministically hit target
    rec = tracker._unpersisted[0]
    rec.target = 104.0
    rec.stop_loss = 98.0

    candle = _candle(3, open=100.0, high=105.0, low=100.0, close=104.5, timeframe="3min")
    resolved = tracker.check_pending(SYMBOL, SEG, candle)
    assert resolved == 1
    assert tracker._resolved_buffer == []  # not yet flushed once, so no UPDATE needed
    assert len(tracker._unpersisted) == 1
    assert tracker._unpersisted[0].outcome == "target_hit"

    inserted, updated = tracker.flush()
    assert (inserted, updated) == (1, 0)
    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "target_hit"


# --------------------------------------------------------------------- same-candle ambiguity

def test_same_candle_ambiguity_resolves_via_1min_data_showing_target_first(session_factory):
    _register_symbol(session_factory)
    _make_prediction(session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)

    with session_factory() as session:
        # 3 real 1-min candles covering the 3-min window: first hits target
        # cleanly, well before the third (which would separately hit stop).
        session.add_all([
            CandleToday(symbol=SYMBOL, exchange_segment=SEG, timeframe="1min",
                        ts=_BASE_TS + timedelta(minutes=3), open_price=100.0, high_price=105.0,
                        low_price=100.0, close_price=104.5, volume=100),
            CandleToday(symbol=SYMBOL, exchange_segment=SEG, timeframe="1min",
                        ts=_BASE_TS + timedelta(minutes=4), open_price=104.5, high_price=104.5,
                        low_price=100.5, close_price=101.0, volume=100),
            CandleToday(symbol=SYMBOL, exchange_segment=SEG, timeframe="1min",
                        ts=_BASE_TS + timedelta(minutes=5), open_price=101.0, high_price=101.5,
                        low_price=97.0, close_price=97.5, volume=100),
        ])
        session.commit()

    # the 3-min candle itself straddles both target (104) and stop (98)
    ambiguous_candle = _candle(3, open=100.0, high=105.0, low=97.0, close=97.5, timeframe="3min")
    resolved = tracker.check_pending(SYMBOL, SEG, ambiguous_candle)
    assert resolved == 1
    tracker.flush()
    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "target_hit"


def test_same_candle_ambiguity_falls_back_to_stop_hit_when_1min_data_is_also_ambiguous(session_factory):
    _register_symbol(session_factory)
    _make_prediction(session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)

    with session_factory() as session:
        # a single 1-min candle that itself touches both levels
        session.add(CandleToday(
            symbol=SYMBOL, exchange_segment=SEG, timeframe="1min",
            ts=_BASE_TS + timedelta(minutes=3), open_price=100.0, high_price=105.0,
            low_price=97.0, close_price=101.0, volume=100,
        ))
        session.commit()

    ambiguous_candle = _candle(3, open=100.0, high=105.0, low=97.0, close=101.0, timeframe="3min")
    tracker.check_pending(SYMBOL, SEG, ambiguous_candle)
    tracker.flush()
    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "stop_hit"


def test_same_candle_ambiguity_falls_back_to_stop_hit_when_no_1min_data_exists(session_factory):
    _register_symbol(session_factory)
    _make_prediction(session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)

    ambiguous_candle = _candle(3, open=100.0, high=105.0, low=97.0, close=101.0, timeframe="3min")
    tracker.check_pending(SYMBOL, SEG, ambiguous_candle)
    tracker.flush()
    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "stop_hit"


def test_same_candle_ambiguity_on_a_1min_prediction_assumes_stop_hit_directly(session_factory):
    _register_symbol(session_factory)
    _make_prediction(session_factory, timeframe="1min", direction="bull", stop_loss=98.0, target=104.0)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)

    ambiguous_candle = _candle(3, open=100.0, high=105.0, low=97.0, close=101.0, timeframe="1min")
    tracker.check_pending(SYMBOL, SEG, ambiguous_candle)
    tracker.flush()
    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "stop_hit"


# --------------------------------------------------------------------- restart / resume

def test_tracker_resumes_pending_predictions_already_open_in_the_db(session_factory):
    """Simulates a live process restarting mid-day: a prediction opened by
    an earlier PredictionTracker instance must still resolve correctly
    under a brand new one, constructed after the row already exists."""
    _register_symbol(session_factory)
    _make_prediction(session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0, candles_checked=5)
    engine = ActivityEngine(session_factory)

    fresh_tracker = PredictionTracker(session_factory, engine)
    assert len(fresh_tracker._pending[(1, "3min")]) == 1
    assert fresh_tracker._pending[(1, "3min")][0].candles_checked == 5

    candle = _candle(20, open=100.0, high=105.0, low=100.0, close=104.5, timeframe="3min")
    resolved = fresh_tracker.check_pending(SYMBOL, SEG, candle)
    assert resolved == 1
    fresh_tracker.flush()
    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "target_hit"
    assert pred.candles_checked == 6  # continued from the loaded count, not reset to 0


def test_tracker_does_not_reopen_a_prediction_already_recorded_by_a_prior_run(session_factory):
    """Simulates re-running the replay script over the same historical day
    in a brand new process: a fresh tracker must recognize the pattern
    already fired at that exact timestamp (even though it's since
    resolved, so _load_existing's pending-only query wouldn't find it) and
    refuse to reopen it."""
    _register_symbol(session_factory)
    predicted_id = _make_prediction(
        session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0,
        pattern="rsi_cross_above_60", outcome="target_hit", outcome_ts=_BASE_TS + timedelta(minutes=3),
    )
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    assert tracker._pending[(1, "3min")] == []  # already resolved, not pending

    activity = {
        "instrument_id": 1, "timeframe": "3min", "ts": _BASE_TS,
        "activity_type": "indicator", "activity": "rsi_cross_above_60", "intensity": 1.0,
        "open_price": 100.0, "high_price": 101.0, "low_price": 99.0, "close_price": 100.0,
    }
    assert tracker.on_activities(SYMBOL, SEG, [activity]) == 0
    tracker.flush()
    with session_factory() as session:
        assert session.query(PatternPrediction).count() == 1
        assert session.query(PatternPrediction).one().id == predicted_id
