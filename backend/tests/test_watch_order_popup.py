from datetime import datetime, timedelta, timezone

from brokers.models import Candle
from db.models import InstrumentActivity, PatternDefinition, PatternOutcome, SubscribedSymbol
from db.ops import LibCandles
from watch_order_popup import (
    intensity_band_for, latest_directional_activity, order_popup_data, volume_stats,
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


def _activity(instrument_id, minutes_offset, activity, intensity=1.0, timeframe=TF):
    return InstrumentActivity(
        instrument_id=instrument_id, timeframe=timeframe, ts=T0 + timedelta(minutes=minutes_offset),
        activity_type="candle_pattern", activity=activity, intensity=intensity,
        open_price=100, high_price=101, low_price=99, close_price=100.5,
    )


def _seed_patterns(session_factory, *code_kind_pairs):
    with session_factory() as session:
        for code, kind in code_kind_pairs:
            session.add(PatternDefinition(code=code, kind=kind, description=code))
        session.commit()


# ------------------------------------------------------------------ latest_directional_activity

def test_finds_the_most_recent_directional_activity(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "hammer"))  # bullish, earlier
        session.add(_activity(instrument_id, 10, "shooting_star"))  # bearish, latest
        session.commit()

    with session_factory() as session:
        result = latest_directional_activity(session, instrument_id, TF)
    assert result.activity == "shooting_star"


def test_ignores_neutral_patterns(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "hammer"))
        session.add(_activity(instrument_id, 10, "doji"))  # neutral, latest, but not directional
        session.commit()

    with session_factory() as session:
        result = latest_directional_activity(session, instrument_id, TF)
    assert result.activity == "hammer"


def test_returns_none_when_nothing_directional_fired(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "doji"))
        session.commit()

    with session_factory() as session:
        assert latest_directional_activity(session, instrument_id, TF) is None


def test_does_not_mix_timeframes(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "hammer", timeframe="1min"))
        session.commit()

    with session_factory() as session:
        assert latest_directional_activity(session, instrument_id, "3min") is None


# ------------------------------------------------------------------ volume_stats

def _candle(symbol, minute, volume):
    return (symbol, "NSE_EQ", Candle(
        symbol=symbol, timeframe="1min", timestamp=T0 + timedelta(minutes=minute),
        open=100, high=101, low=99, close=100.5, volume=volume,
    ))


def test_volume_stats_computes_day_and_last_15min_averages(session_factory):
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol="RELIANCE", exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id="SEC-RELIANCE", previous_close=100.0,
        )
        session.add(row)
        session.commit()

    # 20 one-minute candles, volume = 100 * minute_index (0..19) -- day avg
    # over all 20 differs clearly from the last-15 average.
    LibCandles.persist_bulk(session_factory, [_candle("RELIANCE", i, 100 * i) for i in range(20)])

    with session_factory() as session:
        stats = volume_stats(session, "RELIANCE", "NSE_EQ")
    all_volumes = [100 * i for i in range(20)]
    last_15 = all_volumes[-15:]
    assert stats["avg_volume_day"] == sum(all_volumes) / len(all_volumes)
    assert stats["avg_volume_last_15min"] == sum(last_15) / len(last_15)


def test_volume_stats_is_none_with_no_candles_yet(session_factory):
    with session_factory() as session:
        stats = volume_stats(session, "NOPE", "NSE_EQ")
    assert stats == {"avg_volume_day": None, "avg_volume_last_15min": None}


# ------------------------------------------------------------------ intensity_band_for

def _seed_pattern_outcome_with_intensity(session_factory, instrument_id, day_offset, intensity, favorable, adverse):
    ts = T0 + timedelta(days=day_offset)
    with session_factory() as session:
        session.add(InstrumentActivity(
            instrument_id=instrument_id, timeframe=TF, ts=ts, activity_type="candle_pattern",
            activity="hammer", intensity=intensity, open_price=100, high_price=101, low_price=99, close_price=100.5,
        ))
        session.add(PatternOutcome(
            instrument_id=instrument_id, timeframe=TF, pattern="hammer", activity_type="candle_pattern",
            direction="bull", detected_ts=ts, entry_price=100.0, window_candles=20,
            max_favorable_pct=favorable, max_adverse_pct=adverse,
        ))
        session.commit()


def test_intensity_band_for_matches_the_occurrences_own_band(session_factory):
    instrument_id = _register(session_factory)
    _seed_patterns(session_factory, ("hammer", "single_candle"))
    _seed_pattern_outcome_with_intensity(session_factory, instrument_id, -10, intensity=1.0, favorable=0.01, adverse=-0.005)
    _seed_pattern_outcome_with_intensity(session_factory, instrument_id, -9, intensity=1.1, favorable=0.012, adverse=-0.004)
    _seed_pattern_outcome_with_intensity(session_factory, instrument_id, -8, intensity=1.2, favorable=0.011, adverse=-0.006)
    _seed_pattern_outcome_with_intensity(session_factory, instrument_id, -7, intensity=3.0, favorable=0.03, adverse=-0.01)
    _seed_pattern_outcome_with_intensity(session_factory, instrument_id, -6, intensity=3.1, favorable=0.032, adverse=-0.009)
    _seed_pattern_outcome_with_intensity(session_factory, instrument_id, -5, intensity=3.2, favorable=0.031, adverse=-0.011)
    _seed_pattern_outcome_with_intensity(session_factory, instrument_id, -4, intensity=6.0, favorable=0.06, adverse=-0.02)
    _seed_pattern_outcome_with_intensity(session_factory, instrument_id, -3, intensity=6.1, favorable=0.062, adverse=-0.019)
    _seed_pattern_outcome_with_intensity(session_factory, instrument_id, -2, intensity=6.2, favorable=0.061, adverse=-0.021)

    with session_factory() as session:
        band = intensity_band_for(session, instrument_id, TF, "hammer", intensity=6.05)
    assert band is not None
    assert band["intensity_min"] <= 6.05 <= band["intensity_max"]
    assert band["up_median_pct"] is not None


def test_intensity_band_for_returns_none_with_no_history(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        assert intensity_band_for(session, instrument_id, TF, "hammer", intensity=2.0) is None


# ------------------------------------------------------------------ order_popup_data

def test_order_popup_data_returns_none_for_unknown_instrument(session_factory):
    with session_factory() as session:
        assert order_popup_data(session, 999999, TF) is None


def test_order_popup_data_returns_none_with_no_directional_signal(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        assert order_popup_data(session, instrument_id, TF) is None


def test_order_popup_data_includes_pattern_direction_and_volume(session_factory):
    instrument_id = _register(session_factory)
    _seed_patterns(session_factory, ("hammer", "single_candle"))
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "hammer", intensity=2.0))
        session.commit()
    LibCandles.persist_bulk(session_factory, [_candle("RELIANCE", i, 50) for i in range(5)])

    with session_factory() as session:
        data = order_popup_data(session, instrument_id, TF)
    assert data["pattern"] == "hammer"
    assert data["direction"] == "bull"
    assert data["kind"] == "single_candle"
    assert data["intensity"] == 2.0
    assert data["avg_volume_day"] == 50.0
    assert data["avg_volume_last_15min"] == 50.0
    assert data["intensity_band"] is None  # no PatternOutcome history seeded in this test


def test_order_popup_direction_follows_the_windowed_score_not_the_single_latest_activity(session_factory):
    # Real bug found 2026-10-04: many bearish hits in the window, but the
    # single MOST RECENT activity happens to be bullish -- the popup must
    # still say "bear" (matching the score/button), and its pattern must be
    # the latest BEARISH one, not the lone bullish one that's merely newest.
    instrument_id = _register(session_factory)
    _seed_patterns(session_factory, ("shooting_star", "single_candle"), ("hammer", "single_candle"))
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "shooting_star"))
        session.add(_activity(instrument_id, 1, "shooting_star"))
        session.add(_activity(instrument_id, 2, "shooting_star"))
        session.add(_activity(instrument_id, 3, "hammer"))  # newest, but bull_score(1) < bear_score(3)
        session.commit()

    with session_factory() as session:
        data = order_popup_data(session, instrument_id, TF)
    assert data["direction"] == "bear"
    assert data["pattern"] == "shooting_star"


def test_order_popup_data_returns_none_on_a_tied_score(session_factory):
    instrument_id = _register(session_factory)
    _seed_patterns(session_factory, ("hammer", "single_candle"), ("shooting_star", "single_candle"))
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "hammer"))
        session.add(_activity(instrument_id, 1, "shooting_star"))
        session.commit()

    with session_factory() as session:
        assert order_popup_data(session, instrument_id, TF) is None


def test_order_popup_data_omits_intensity_band_for_non_candle_patterns(session_factory):
    instrument_id = _register(session_factory)
    _seed_patterns(session_factory, ("macd_bullish_cross", "indicator"))
    with session_factory() as session:
        session.add(_activity(instrument_id, 0, "macd_bullish_cross", intensity=None))
        session.commit()

    with session_factory() as session:
        data = order_popup_data(session, instrument_id, TF)
    assert data["kind"] == "indicator"
    assert data["intensity_band"] is None
