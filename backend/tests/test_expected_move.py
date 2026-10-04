from datetime import datetime, timedelta, timezone

from db.models import CandleIndicators, InstrumentActivity, PatternOutcome, SubscribedSymbol
from expected_move import (
    backtested_target_distance, geometric_target_distance, suggested_quantity, suggested_sl_and_target,
)

T0 = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
TF = "3min"


def _register(session_factory, symbol="RELIANCE") -> int:
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id=f"SEC-{symbol}", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _activity(instrument_id, activity, high_price=101, low_price=99, close_price=100.5,
              neckline_price=None, stop_loss_price=None, target_price=None):
    return InstrumentActivity(
        instrument_id=instrument_id, timeframe=TF, ts=T0, activity_type="candle_pattern",
        activity=activity, intensity=1.0, open_price=100, high_price=high_price,
        low_price=low_price, close_price=close_price,
        neckline_price=neckline_price, stop_loss_price=stop_loss_price, target_price=target_price,
    )


# ------------------------------------------------------------------ geometric_target_distance

def test_vwap_rejection_bull_targets_the_upper_band(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(CandleIndicators(instrument_id=instrument_id, timeframe=TF, ts=T0, bb_upper=110, bb_lower=90))
        session.commit()

    with session_factory() as session:
        activity = _activity(instrument_id, "vwap_rejection_bull")
        distance = geometric_target_distance(session, instrument_id, TF, activity, "bull", ltp=100.0)
    assert distance == 10.0  # 110 - 100


def test_vwap_rejection_bear_targets_the_lower_band(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(CandleIndicators(instrument_id=instrument_id, timeframe=TF, ts=T0, bb_upper=110, bb_lower=90))
        session.commit()

    with session_factory() as session:
        activity = _activity(instrument_id, "vwap_rejection_bear_strong")
        distance = geometric_target_distance(session, instrument_id, TF, activity, "bear", ltp=100.0)
    assert distance == 10.0  # 100 - 90


def test_vwap_rejection_is_none_without_bb_data(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        activity = _activity(instrument_id, "vwap_rejection_bull")
        assert geometric_target_distance(session, instrument_id, TF, activity, "bull", ltp=100.0) is None


def test_structure_bull_uses_the_swing_highs_own_price(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        activity = _activity(instrument_id, "bullish_break_of_structure", high_price=115, low_price=95)
        distance = geometric_target_distance(session, instrument_id, TF, activity, "bull", ltp=100.0)
    assert distance == 15.0  # |115 - 100|


def test_structure_bear_uses_the_swing_lows_own_price(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        activity = _activity(instrument_id, "bearish_break_of_structure", high_price=105, low_price=80)
        distance = geometric_target_distance(session, instrument_id, TF, activity, "bear", ltp=100.0)
    assert distance == 20.0  # |80 - 100|


def test_candlestick_pattern_has_no_geometric_distance(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        activity = _activity(instrument_id, "hammer")
        assert geometric_target_distance(session, instrument_id, TF, activity, "bull", ltp=100.0) is None


def test_graph_formation_has_no_geometric_distance_yet(session_factory):
    # double_top DOES have real neckline geometry in activity_engine.py, but
    # it's not reconstructable from stored data -- see module docstring.
    instrument_id = _register(session_factory)
    with session_factory() as session:
        activity = _activity(instrument_id, "double_top")
        assert geometric_target_distance(session, instrument_id, TF, activity, "bear", ltp=100.0) is None


# ------------------------------------------------------------------ backtested_target_distance

def _seed_outcome(session_factory, instrument_id, day_offset, direction, favorable, adverse, pattern="hammer"):
    with session_factory() as session:
        session.add(PatternOutcome(
            instrument_id=instrument_id, timeframe=TF, pattern=pattern, activity_type="candle_pattern",
            direction=direction, detected_ts=T0 + timedelta(days=day_offset), entry_price=100.0,
            window_candles=20, max_favorable_pct=favorable, max_adverse_pct=adverse,
        ))
        session.commit()


def test_backtested_distance_bull_uses_up_median(session_factory):
    instrument_id = _register(session_factory)
    _seed_outcome(session_factory, instrument_id, -1, "bull", favorable=0.02, adverse=-0.01)
    _seed_outcome(session_factory, instrument_id, -2, "bull", favorable=0.04, adverse=-0.01)

    with session_factory() as session:
        distance = backtested_target_distance(session, instrument_id, TF, "hammer", "bull", ltp=100.0)
    assert distance == 3.0  # median favorable 0.03 * 100


def test_backtested_distance_bear_uses_down_median_magnitude(session_factory):
    instrument_id = _register(session_factory)
    _seed_outcome(session_factory, instrument_id, -1, "bear", favorable=0.01, adverse=-0.02)
    _seed_outcome(session_factory, instrument_id, -2, "bear", favorable=0.01, adverse=-0.04)

    with session_factory() as session:
        distance = backtested_target_distance(session, instrument_id, TF, "hammer", "bear", ltp=100.0)
    assert distance == 3.0  # median adverse -0.03, magnitude * 100


def test_backtested_distance_is_none_with_no_history(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        assert backtested_target_distance(session, instrument_id, TF, "hammer", "bull", ltp=100.0) is None


# ------------------------------------------------------------------ suggested_sl_and_target

def test_sl_is_always_atr_based(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        activity = _activity(instrument_id, "hammer")
        result = suggested_sl_and_target(session, instrument_id, TF, activity, "bull", ltp=100.0, atr=2.0)
    assert result["sl_price"] == 97.0  # 100 - 2.0*1.5


def test_target_prefers_geometric_over_backtested_when_smaller(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(CandleIndicators(instrument_id=instrument_id, timeframe=TF, ts=T0, bb_upper=105, bb_lower=90))
        session.commit()
    _seed_outcome(session_factory, instrument_id, -1, "bull", favorable=0.10, adverse=-0.01,
                  pattern="vwap_rejection_bull")  # backtested: 10.0 distance

    with session_factory() as session:
        activity = _activity(instrument_id, "vwap_rejection_bull")
        result = suggested_sl_and_target(session, instrument_id, TF, activity, "bull", ltp=100.0, atr=2.0)
    # geometric (105-100=5) < backtested (0.10*100=10) -> geometric wins
    assert result["target_price"] == 105.0
    assert result["target_source"] == "geometric"


def test_target_prefers_backtested_over_geometric_when_smaller(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(CandleIndicators(instrument_id=instrument_id, timeframe=TF, ts=T0, bb_upper=150, bb_lower=50))
        session.commit()
    _seed_outcome(session_factory, instrument_id, -1, "bull", favorable=0.02, adverse=-0.01,
                  pattern="vwap_rejection_bull")  # backtested: 2.0 distance

    with session_factory() as session:
        activity = _activity(instrument_id, "vwap_rejection_bull")
        result = suggested_sl_and_target(session, instrument_id, TF, activity, "bull", ltp=100.0, atr=2.0)
    # geometric (150-100=50) > backtested (0.02*100=2) -> backtested wins
    assert result["target_price"] == 102.0
    assert result["target_source"] == "backtested"


def test_target_falls_back_to_atr_when_neither_option_available(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        activity = _activity(instrument_id, "hammer")  # no geometry, no PatternOutcome seeded
        result = suggested_sl_and_target(session, instrument_id, TF, activity, "bull", ltp=100.0, atr=2.0)
    # ATR fallback: entry + atr*1.5*2.0 = 100 + 6.0
    assert result["target_price"] == 106.0
    assert result["target_source"] == "atr_fallback"


def test_sl_and_target_are_none_without_atr_and_no_other_data(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        activity = _activity(instrument_id, "hammer")
        result = suggested_sl_and_target(session, instrument_id, TF, activity, "bull", ltp=100.0, atr=None)
    assert result == {"sl_price": None, "target_price": None, "target_source": None}


def test_graph_formation_with_stored_geometry_uses_it_directly(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        activity = _activity(
            instrument_id, "double_top", neckline_price=315.0, stop_loss_price=318.6, target_price=312.3,
        )
        # geometric/backtested distances would be computed off a totally
        # different LTP (250) -- if the neckline shortcut works, the stored
        # values win untouched, never recombined with a distance from ltp.
        result = suggested_sl_and_target(session, instrument_id, TF, activity, "bear", ltp=250.0, atr=99.0)
    assert result == {"sl_price": 318.6, "target_price": 312.3, "target_source": "neckline"}


def test_graph_formation_without_stored_geometry_falls_through_to_atr(session_factory):
    # A pre-2026-10-05 row (or any row where detection somehow left these
    # NULL) -- double_top is still in _GRAPH_FORMATION_PATTERNS, but with no
    # stored levels it must fall through to the normal distance logic, not
    # silently return None/None.
    instrument_id = _register(session_factory)
    with session_factory() as session:
        activity = _activity(instrument_id, "double_top")  # neckline/stop/target all None
        result = suggested_sl_and_target(session, instrument_id, TF, activity, "bear", ltp=100.0, atr=2.0)
    assert result["target_source"] == "atr_fallback"
    assert result["target_price"] == 100.0 - 2.0 * 1.5 * 2.0


# ------------------------------------------------------------------ suggested_quantity

def test_suggested_quantity_divides_by_the_default_divisor():
    assert suggested_quantity(avg_volume_last_15min=2000) == 100  # 2000/20


def test_suggested_quantity_is_at_least_1():
    assert suggested_quantity(avg_volume_last_15min=5) == 1  # 5/20 rounds to 0, floored at 1


def test_suggested_quantity_is_none_without_volume_data():
    assert suggested_quantity(avg_volume_last_15min=None) is None
    assert suggested_quantity(avg_volume_last_15min=0) is None
