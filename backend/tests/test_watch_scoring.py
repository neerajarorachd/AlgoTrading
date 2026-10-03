from datetime import datetime, timedelta, timezone

from db.models import CandleIndicators, InstrumentActivity, PatternDefinition, SubscribedSymbol
from watch_scoring import (
    BUCKET_COLORS, RANGE_BASELINE_CANDLES, classify_bucket, load_pattern_weights, score_instrument,
    score_instruments, volatility_factor,
)

T0 = datetime(2026, 10, 4, 4, 0, tzinfo=timezone.utc)
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


def _seed_patterns(session_factory, *code_kind_pairs):
    with session_factory() as session:
        for code, kind in code_kind_pairs:
            session.add(PatternDefinition(code=code, kind=kind, description=code))
        session.commit()


def _activity(instrument_id, candle_index, activity, timeframe=TF):
    ts = T0 + timedelta(minutes=3 * candle_index)
    return InstrumentActivity(
        instrument_id=instrument_id, timeframe=timeframe, ts=ts,
        activity_type="candle_pattern", activity=activity, intensity=1.0,
        open_price=100, high_price=101, low_price=99, close_price=100.5,
    )


# ------------------------------------------------------------------ load_pattern_weights

def test_load_pattern_weights_maps_kind_to_the_configured_weight(session_factory):
    _seed_patterns(session_factory, ("doji", "single_candle"), ("double_top", "graph_formation"))
    with session_factory() as session:
        weights = load_pattern_weights(session)
    assert weights["doji"] == 1
    assert weights["double_top"] == 4


def test_load_pattern_weights_unknown_kind_falls_back_to_1(session_factory):
    _seed_patterns(session_factory, ("mystery", "not_a_real_kind"))
    with session_factory() as session:
        weights = load_pattern_weights(session)
    assert weights["mystery"] == 1


# ------------------------------------------------------------------ classify_bucket

def test_classify_bucket_quiet_when_both_zero():
    assert classify_bucket(0, 0) == "quiet"


def test_classify_bucket_mild_bull_below_threshold():
    assert classify_bucket(3, 0) == "mild_bull"


def test_classify_bucket_strong_bull_at_or_above_threshold():
    assert classify_bucket(6, 0) == "strong_bull"


def test_classify_bucket_mild_bear_below_threshold():
    assert classify_bucket(0, 3) == "mild_bear"


def test_classify_bucket_strong_bear_at_or_above_threshold():
    assert classify_bucket(1, 6) == "strong_bear"


def test_classify_bucket_choppy_when_both_sides_active_and_close():
    assert classify_bucket(4, 3) == "choppy"


def test_classify_bucket_not_choppy_when_one_side_clearly_dominates():
    assert classify_bucket(10, 1) == "strong_bull"


def test_every_bucket_classify_bucket_can_return_has_a_color():
    for bucket in ("quiet", "mild_bull", "strong_bull", "mild_bear", "strong_bear", "choppy"):
        assert bucket in BUCKET_COLORS


# ------------------------------------------------------------------ score_instrument

def test_bullish_pattern_scores_bull_only(session_factory):
    _seed_patterns(session_factory, ("hammer", "single_candle"))
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "hammer"))  # bullish, weight 1
        session.commit()

    with session_factory() as session:
        result = score_instrument(session, instrument_id, TF, 10, load_pattern_weights(session))
    assert result["bull_score"] == 1 and result["bear_score"] == 0
    assert result["bucket"] == "mild_bull"


def test_bearish_pattern_scores_bear_only(session_factory):
    _seed_patterns(session_factory, ("shooting_star", "single_candle"))
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "shooting_star"))
        session.commit()

    with session_factory() as session:
        result = score_instrument(session, instrument_id, TF, 10, load_pattern_weights(session))
    assert result["bull_score"] == 0 and result["bear_score"] == 1


def test_neutral_pattern_scores_neither_side(session_factory):
    _seed_patterns(session_factory, ("doji", "single_candle"))
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "doji"))  # not in BULLISH_PATTERNS or BEARISH_PATTERNS
        session.commit()

    with session_factory() as session:
        result = score_instrument(session, instrument_id, TF, 10, load_pattern_weights(session))
    assert result == {
        "instrument_id": instrument_id, "bull_score": 0, "bear_score": 0, "bucket": "quiet", "volatility_factor": 1.0,
    }


def test_same_pattern_on_different_candles_counts_each_occurrence(session_factory):
    _seed_patterns(session_factory, ("hammer", "single_candle"))
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "hammer"))
        session.add(_activity(instrument_id, 1, "hammer"))
        session.add(_activity(instrument_id, 2, "hammer"))
        session.commit()

    with session_factory() as session:
        result = score_instrument(session, instrument_id, TF, 10, load_pattern_weights(session))
    assert result["bull_score"] == 3  # 3 separate occurrences, each weight 1


def test_higher_weight_kind_contributes_more_per_occurrence(session_factory):
    _seed_patterns(session_factory, ("bullish_break_of_structure", "structure"))  # weight 3
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "bullish_break_of_structure"))
        session.commit()

    with session_factory() as session:
        result = score_instrument(session, instrument_id, TF, 10, load_pattern_weights(session))
    assert result["bull_score"] == 3


def test_activity_outside_the_window_is_excluded(session_factory):
    _seed_patterns(session_factory, ("hammer", "single_candle"))
    instrument_id = _register(session_factory)
    with session_factory() as session:
        # 12 distinct candles of activity; window=10 should only see the most recent 10
        for i in range(12):
            session.add(_activity(instrument_id, i, "hammer"))
        session.commit()

    with session_factory() as session:
        result = score_instrument(session, instrument_id, TF, 10, load_pattern_weights(session))
    assert result["bull_score"] == 10  # the 2 oldest candles excluded


def test_different_timeframe_is_not_mixed_in(session_factory):
    _seed_patterns(session_factory, ("hammer", "single_candle"))
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "hammer", timeframe="3min"))
        session.add(_activity(instrument_id, 0, "hammer", timeframe="1min"))
        session.commit()

    with session_factory() as session:
        result = score_instrument(session, instrument_id, "3min", 10, load_pattern_weights(session))
    assert result["bull_score"] == 1


def test_different_instrument_is_not_mixed_in(session_factory):
    _seed_patterns(session_factory, ("hammer", "single_candle"))
    a = _register(session_factory, symbol="RELIANCE")
    b = _register(session_factory, symbol="TCS")
    with session_factory() as session:
        session.add(_activity(a, 0, "hammer"))
        session.commit()

    with session_factory() as session:
        result_a = score_instrument(session, a, TF, 10, load_pattern_weights(session))
        result_b = score_instrument(session, b, TF, 10, load_pattern_weights(session))
    assert result_a["bull_score"] == 1
    assert result_b["bull_score"] == 0


def test_an_instrument_with_no_activity_at_all_is_quiet(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        result = score_instrument(session, instrument_id, TF, 10, load_pattern_weights(session))
    assert result["bucket"] == "quiet"


# ------------------------------------------------------------------ score_instruments (batch)

def test_score_instruments_scores_each_one_independently(session_factory):
    _seed_patterns(session_factory, ("hammer", "single_candle"), ("shooting_star", "single_candle"))
    a = _register(session_factory, symbol="RELIANCE")
    b = _register(session_factory, symbol="TCS")
    with session_factory() as session:
        session.add(_activity(a, 0, "hammer"))
        session.add(_activity(b, 0, "shooting_star"))
        session.commit()

    with session_factory() as session:
        results = {r["instrument_id"]: r for r in score_instruments(session, [a, b], TF)}
    assert results[a]["bucket"] == "mild_bull"
    assert results[b]["bucket"] == "mild_bear"


# ------------------------------------------------------------------ volatility_factor

def _indicator_row(instrument_id, candle_index, atr=None, bb_upper=None, bb_middle=None, bb_lower=None):
    return CandleIndicators(
        instrument_id=instrument_id, timeframe=TF, ts=T0 + timedelta(minutes=3 * candle_index),
        atr=atr, bb_upper=bb_upper, bb_middle=bb_middle, bb_lower=bb_lower,
    )


def test_volatility_factor_is_neutral_with_fewer_than_2_rows(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_indicator_row(instrument_id, 0, atr=5.0))
        session.commit()

    with session_factory() as session:
        assert volatility_factor(session, instrument_id, TF) == 1.0


def test_volatility_factor_is_neutral_with_no_indicator_rows_at_all(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        assert volatility_factor(session, instrument_id, TF) == 1.0


def test_volatility_factor_above_1_when_atr_expanded_above_its_baseline(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        for i in range(RANGE_BASELINE_CANDLES - 1):
            session.add(_indicator_row(instrument_id, i, atr=2.0))  # flat baseline
        session.add(_indicator_row(instrument_id, RANGE_BASELINE_CANDLES - 1, atr=6.0))  # latest, 3x baseline
        session.commit()

    with session_factory() as session:
        factor = volatility_factor(session, instrument_id, TF)
    assert factor > 1.0


def test_volatility_factor_below_1_when_atr_contracted_below_its_baseline(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        for i in range(RANGE_BASELINE_CANDLES - 1):
            session.add(_indicator_row(instrument_id, i, atr=4.0))
        session.add(_indicator_row(instrument_id, RANGE_BASELINE_CANDLES - 1, atr=1.0))  # latest, well below baseline
        session.commit()

    with session_factory() as session:
        factor = volatility_factor(session, instrument_id, TF)
    assert factor < 1.0


def test_volatility_factor_uses_bb_width_when_atr_is_missing(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        for i in range(RANGE_BASELINE_CANDLES - 1):
            session.add(_indicator_row(instrument_id, i, bb_upper=102, bb_middle=100, bb_lower=98))  # width 0.04
        # latest: much wider bands -- width (110-90)/100 = 0.20
        session.add(_indicator_row(instrument_id, RANGE_BASELINE_CANDLES - 1, bb_upper=110, bb_middle=100, bb_lower=90))
        session.commit()

    with session_factory() as session:
        factor = volatility_factor(session, instrument_id, TF)
    assert factor > 1.0


def test_volatility_factor_is_clamped_to_the_configured_range(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        for i in range(RANGE_BASELINE_CANDLES - 1):
            session.add(_indicator_row(instrument_id, i, atr=1.0))
        session.add(_indicator_row(instrument_id, RANGE_BASELINE_CANDLES - 1, atr=100.0))  # extreme spike
        session.commit()

    with session_factory() as session:
        factor = volatility_factor(session, instrument_id, TF)
    assert factor == 2.0  # VOLATILITY_FACTOR_MAX, not the raw ~100x ratio


def test_score_instrument_scales_both_bull_and_bear_by_the_same_volatility_factor(session_factory):
    _seed_patterns(session_factory, ("hammer", "single_candle"), ("shooting_star", "single_candle"))
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "hammer"))
        session.add(_activity(instrument_id, 1, "shooting_star"))
        for i in range(RANGE_BASELINE_CANDLES - 1):
            session.add(_indicator_row(instrument_id, i, atr=2.0))
        session.add(_indicator_row(instrument_id, RANGE_BASELINE_CANDLES - 1, atr=4.0))  # 2x baseline -> clamped factor 2.0
        session.commit()

    with session_factory() as session:
        result = score_instrument(session, instrument_id, TF, 10, load_pattern_weights(session))
    assert result["volatility_factor"] == 2.0
    assert result["bull_score"] == 2.0  # 1 (hammer weight) * 2.0
    assert result["bear_score"] == 2.0  # 1 (shooting_star weight) * 2.0
    # direction/bucket comparison is unaffected -- both sides scaled equally, still tied -> choppy
    assert result["bucket"] == "choppy"
