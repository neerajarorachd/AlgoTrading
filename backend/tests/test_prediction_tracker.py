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

    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.pattern == "rsi_cross_above_60"
    assert pred.direction == "bull"
    assert pred.neckline is None
    assert float(pred.stop_loss) == pytest.approx(crossover_stop_loss(100.0, atr, "bull", tracker.atr_multiplier))
    assert float(pred.target) == pytest.approx(crossover_target(100.0, atr, "bull", tracker.atr_multiplier, tracker.risk_reward_ratio))


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

    with session_factory() as session:
        pred = session.query(PatternPrediction).filter_by(pattern="double_top").one()
    assert float(pred.neckline) == pytest.approx(expected_neckline)
    assert float(pred.stop_loss) == pytest.approx(expected_stop)
    assert float(pred.target) == pytest.approx(expected_target)
    assert pred.direction == "bear"


# --------------------------------------------------------------------- check_pending

def test_check_pending_resolves_target_hit_for_a_bullish_prediction(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    _make_prediction(session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0)

    candle = _candle(3, open=100.0, high=105.0, low=100.0, close=104.5, timeframe="3min")
    resolved = tracker.check_pending(SYMBOL, SEG, candle)
    assert resolved == 1

    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "target_hit"
    assert pred.candles_checked == 1


def test_check_pending_resolves_stop_hit_for_a_bearish_prediction(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    _make_prediction(session_factory, timeframe="3min", direction="bear", stop_loss=104.0, target=96.0)

    candle = _candle(3, open=100.0, high=105.0, low=99.0, close=104.5, timeframe="3min")
    resolved = tracker.check_pending(SYMBOL, SEG, candle)
    assert resolved == 1

    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "stop_hit"


def test_check_pending_leaves_a_prediction_open_when_neither_level_is_hit(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    _make_prediction(session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0)

    candle = _candle(3, open=100.0, high=101.0, low=99.5, close=100.5, timeframe="3min")
    resolved = tracker.check_pending(SYMBOL, SEG, candle)
    assert resolved == 0

    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome is None
    assert pred.candles_checked == 1


def test_check_pending_times_out_as_sideways_after_the_scan_window(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    _make_prediction(
        session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0,
        candles_checked=OUTCOME_SCAN_CANDLES - 1,
    )

    candle = _candle(3, open=100.0, high=101.0, low=99.5, close=100.5, timeframe="3min")
    resolved = tracker.check_pending(SYMBOL, SEG, candle)
    assert resolved == 1

    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "sideways"
    assert pred.candles_checked == OUTCOME_SCAN_CANDLES


def test_check_pending_does_not_resolve_against_the_candle_it_was_detected_on(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    detected_ts = _BASE_TS + timedelta(minutes=9)
    _make_prediction(
        session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0,
        detected_ts=detected_ts,
    )

    # same timestamp as detected_ts — must NOT resolve against its own candle
    same_candle = Candle(
        symbol=SYMBOL, timeframe="3min", timestamp=detected_ts,
        open=100.0, high=105.0, low=99.0, close=104.5, volume=100,
    )
    resolved = tracker.check_pending(SYMBOL, SEG, same_candle)
    assert resolved == 0


# --------------------------------------------------------------------- same-candle ambiguity

def test_same_candle_ambiguity_resolves_via_1min_data_showing_target_first(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    _make_prediction(session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0)

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
    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "target_hit"


def test_same_candle_ambiguity_falls_back_to_stop_hit_when_1min_data_is_also_ambiguous(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    _make_prediction(session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0)

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
    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "stop_hit"


def test_same_candle_ambiguity_falls_back_to_stop_hit_when_no_1min_data_exists(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    _make_prediction(session_factory, timeframe="3min", direction="bull", stop_loss=98.0, target=104.0)

    ambiguous_candle = _candle(3, open=100.0, high=105.0, low=97.0, close=101.0, timeframe="3min")
    tracker.check_pending(SYMBOL, SEG, ambiguous_candle)
    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "stop_hit"


def test_same_candle_ambiguity_on_a_1min_prediction_assumes_stop_hit_directly(session_factory):
    _register_symbol(session_factory)
    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)
    _make_prediction(session_factory, timeframe="1min", direction="bull", stop_loss=98.0, target=104.0)

    ambiguous_candle = _candle(3, open=100.0, high=105.0, low=97.0, close=101.0, timeframe="1min")
    tracker.check_pending(SYMBOL, SEG, ambiguous_candle)
    with session_factory() as session:
        pred = session.query(PatternPrediction).one()
    assert pred.outcome == "stop_hit"
