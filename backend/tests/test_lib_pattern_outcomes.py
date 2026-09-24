from datetime import datetime, timezone

from db.models import InstrumentActivity, PatternOutcome, SubscribedSymbol
from db.ops import LibPatternOutcomes

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"


def _dt(d, h=9, mi=15):
    return datetime(2026, 1, d, h, mi, tzinfo=timezone.utc)


def _register(session_factory) -> int:
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="1333", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _outcome(instrument_id, ts, pattern, activity_type="candle_pattern", direction="bull",
             entry_price=100.0, pct_change_5=None, pct_change_10=None, pct_change_15=None,
             pct_change_20=None, pct_change_30=None, max_favorable_pct=None, max_adverse_pct=None,
             timeframe="1min", window_candles=30, entry_rsi=None, entry_rsi_state=None, entry_rsi_trend=None,
             entry_macd_line=None, entry_macd_signal=None, entry_macd_state=None, entry_macd_trend=None,
             entry_stoch_k=None, entry_stoch_state=None, entry_stoch_trend=None):
    return PatternOutcome(
        instrument_id=instrument_id, timeframe=timeframe, pattern=pattern, activity_type=activity_type,
        direction=direction, detected_ts=ts, entry_price=entry_price,
        pct_change_5=pct_change_5, pct_change_10=pct_change_10, pct_change_15=pct_change_15,
        pct_change_20=pct_change_20, pct_change_30=pct_change_30,
        max_favorable_pct=max_favorable_pct, max_adverse_pct=max_adverse_pct,
        window_candles=window_candles,
        entry_rsi=entry_rsi, entry_rsi_state=entry_rsi_state, entry_rsi_trend=entry_rsi_trend,
        entry_macd_line=entry_macd_line, entry_macd_signal=entry_macd_signal,
        entry_macd_state=entry_macd_state, entry_macd_trend=entry_macd_trend,
        entry_stoch_k=entry_stoch_k, entry_stoch_state=entry_stoch_state, entry_stoch_trend=entry_stoch_trend,
    )


def _activity(instrument_id, ts, activity, intensity, timeframe="1min", activity_type="candle_pattern"):
    return InstrumentActivity(
        instrument_id=instrument_id, timeframe=timeframe, ts=ts,
        activity_type=activity_type, activity=activity, intensity=intensity,
        open_price=100, high_price=101, low_price=99, close_price=100.5,
    )


# --------------------------------------------------------------------- raw_values_by_pattern

def test_raw_values_by_pattern_groups_and_computes_range(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([
            _outcome(instrument_id, _dt(1), "hammer", pct_change_20=0.02, pct_change_30=0.025,
                     max_favorable_pct=0.03, max_adverse_pct=-0.01),
            _outcome(instrument_id, _dt(2), "hammer", pct_change_20=-0.01, pct_change_30=-0.015,
                     max_favorable_pct=0.015, max_adverse_pct=-0.012),
        ])
        session.commit()

    with session_factory() as session:
        by_pattern = LibPatternOutcomes.raw_values_by_pattern(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0),
        )
    hammer = by_pattern["hammer"]
    assert hammer["pct_values"][20] == [0.02, -0.01]
    assert hammer["pct_values"][30] == [0.025, -0.015]
    assert hammer["pct_values"][5] == []  # never set on these fixtures
    assert hammer["up_values"] == [0.03, 0.015]
    assert hammer["down_values"] == [-0.01, -0.012]
    assert hammer["range_values"] == [0.03 - (-0.01), 0.015 - (-0.012)]


def test_raw_values_by_pattern_excludes_nulls_from_each_checkpoint_independently(session_factory):
    """A row with pct_change_20=None (too close to the end of the data)
    should be excluded from that checkpoint's list but still contribute to
    up/down/range if those happen to be present, and vice versa — and a
    null at one checkpoint doesn't affect a different checkpoint's list."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([
            _outcome(instrument_id, _dt(1), "hammer", pct_change_20=None, pct_change_30=0.04,
                     max_favorable_pct=0.02, max_adverse_pct=-0.01),
            _outcome(instrument_id, _dt(2), "hammer", pct_change_20=0.01, pct_change_30=None,
                     max_favorable_pct=None, max_adverse_pct=None),
        ])
        session.commit()

    with session_factory() as session:
        by_pattern = LibPatternOutcomes.raw_values_by_pattern(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0),
        )
    hammer = by_pattern["hammer"]
    assert hammer["pct_values"][20] == [0.01]
    assert hammer["pct_values"][30] == [0.04]
    assert hammer["up_values"] == [0.02]
    assert hammer["down_values"] == [-0.01]
    assert hammer["range_values"] == [0.02 - (-0.01)]


def test_raw_values_by_pattern_filters_by_date_range_timeframe_and_patterns(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([
            _outcome(instrument_id, _dt(1), "hammer", pct_change_20=0.02),
            _outcome(instrument_id, _dt(20), "hammer", pct_change_20=0.05),  # out of range
            _outcome(instrument_id, _dt(1), "hammer", pct_change_20=0.09, timeframe="5min"),  # wrong timeframe
            _outcome(instrument_id, _dt(1), "doji", pct_change_20=0.5, direction=None),  # filtered out by patterns
        ])
        session.commit()

    with session_factory() as session:
        by_pattern = LibPatternOutcomes.raw_values_by_pattern(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(10, 0, 0), patterns=["hammer"],
        )
    assert list(by_pattern.keys()) == ["hammer"]
    assert by_pattern["hammer"]["pct_values"][20] == [0.02]


def test_raw_values_by_pattern_returns_empty_dict_when_nothing_analyzed_yet(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        by_pattern = LibPatternOutcomes.raw_values_by_pattern(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0),
        )
    assert by_pattern == {}


# --------------------------------------------------------------------- intensity_banded_analysis

def _seed_paired(session_factory, instrument_id, occurrences):
    """occurrences: [(ts, intensity, direction, pct_change_20, up, down), ...] —
    writes the matching InstrumentActivity + PatternOutcome pair for each,
    same (instrument_id, timeframe, pattern=activity, ts=detected_ts) key
    intensity_banded_analysis joins on."""
    with session_factory() as session:
        for ts, intensity, direction, pct20, up, down in occurrences:
            session.add(_activity(instrument_id, ts, "hammer", intensity))
            session.add(_outcome(
                instrument_id, ts, "hammer", direction=direction,
                pct_change_20=pct20, max_favorable_pct=up, max_adverse_pct=down,
            ))
        session.commit()


def test_intensity_banded_analysis_splits_into_equal_count_bands_low_to_high(session_factory):
    instrument_id = _register(session_factory)
    # 6 occurrences, intensities 1..6, split into 3 bands of 2 each
    _seed_paired(session_factory, instrument_id, [
        (_dt(1, 9, i), float(i), "bull", 0.01, 0.02, -0.01) for i in range(1, 7)
    ])

    with session_factory() as session:
        bands = LibPatternOutcomes.intensity_banded_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer",
        )
    assert len(bands) == 3
    assert [b["count"] for b in bands] == [2, 2, 2]
    assert bands[0]["intensity_min"] == 1.0 and bands[0]["intensity_max"] == 2.0
    assert bands[1]["intensity_min"] == 3.0 and bands[1]["intensity_max"] == 4.0
    assert bands[2]["intensity_min"] == 5.0 and bands[2]["intensity_max"] == 6.0


def test_intensity_banded_analysis_computes_match_rate_per_checkpoint(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        # High intensity band (only occurrence): bullish, closed up -> matched
        session.add(_activity(instrument_id, _dt(1, 9, 0), "hammer", 5.0))
        session.add(_outcome(instrument_id, _dt(1, 9, 0), "hammer", direction="bull",
                              pct_change_5=0.01, pct_change_20=0.02, pct_change_30=None))
        # Low intensity band (only occurrence): bullish, closed down -> not matched
        session.add(_activity(instrument_id, _dt(1, 9, 1), "hammer", 1.0))
        session.add(_outcome(instrument_id, _dt(1, 9, 1), "hammer", direction="bull",
                              pct_change_5=-0.01, pct_change_20=-0.02, pct_change_30=None))
        session.commit()

    with session_factory() as session:
        bands = LibPatternOutcomes.intensity_banded_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer", bands=2,
        )
    assert len(bands) == 2
    low_band, high_band = bands  # sorted ascending by intensity
    assert low_band["checkpoints"][5]["matched"] == 0
    assert low_band["checkpoints"][5]["total"] == 1
    assert low_band["checkpoints"][30]["total"] == 0  # None on this fixture
    assert high_band["checkpoints"][5]["matched"] == 1
    assert high_band["checkpoints"][5]["total"] == 1
    assert high_band["checkpoints"][20]["pct"] == 1.0


def test_intensity_banded_analysis_excludes_occurrences_with_null_intensity(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, _dt(1, 9, 0), "hammer", None))  # no intensity formula
        session.add(_outcome(instrument_id, _dt(1, 9, 0), "hammer", pct_change_20=0.02))
        session.commit()

    with session_factory() as session:
        bands = LibPatternOutcomes.intensity_banded_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer",
        )
    assert bands == []


def test_intensity_banded_analysis_returns_empty_when_no_outcome_rows(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_activity(instrument_id, _dt(1, 9, 0), "hammer", 2.0))
        session.commit()  # activity exists, but no matching PatternOutcome row

    with session_factory() as session:
        bands = LibPatternOutcomes.intensity_banded_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer",
        )
    assert bands == []


# --------------------------------------------------------------------- pattern_level_analysis

def test_pattern_level_analysis_computes_overall_win_rate_no_intensity_needed(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([
            _outcome(instrument_id, _dt(1), "hammer", pct_change_20=0.02),
            _outcome(instrument_id, _dt(2), "hammer", pct_change_20=0.03),
            _outcome(instrument_id, _dt(3), "hammer", pct_change_20=-0.01),
        ])
        session.commit()

    with session_factory() as session:
        stats = LibPatternOutcomes.pattern_level_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer",
        )
    assert stats["count"] == 3
    assert stats["checkpoints"][20] == {"matched": 2, "total": 3, "pct": 2 / 3}


def test_pattern_level_analysis_returns_none_when_nothing_analyzed_yet(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        stats = LibPatternOutcomes.pattern_level_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer",
        )
    assert stats is None


# combination_analysis/ALL_COMBO_DIMENSIONS (RS2's own joint-match engine)
# were removed 2026-09-16 alongside RS2 itself — see guiding_scenarios.py
# and recommendation_engine.py's own docstrings for the redesign. The
# equal-count intensity banding they were built on lives on via
# _band_outcomes_from_paired/InstrumentHistoryCache.band_outcomes, tested
# below (guiding_scenarios.py's own test file covers its actual consumer).


# --------------------------------------------------------------------- InstrumentHistoryCache

def test_instrument_history_cache_matches_per_call_results_for_multiple_patterns(session_factory):
    """The whole point of the cache: identical results to the query-per-
    call functions, for MULTIPLE patterns, off of the same 2 bulk queries
    (not one query per pattern)."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([
            _activity(instrument_id, _dt(1, 9, 0), "hammer", 2.0),
            _outcome(instrument_id, _dt(1, 9, 0), "hammer", pct_change_20=0.02,
                     entry_rsi_state="neutral", entry_macd_state="bearish"),
            _activity(instrument_id, _dt(2, 9, 0), "hammer", 3.0),
            _outcome(instrument_id, _dt(2, 9, 0), "hammer", pct_change_20=-0.01,
                     entry_rsi_state="overbought", entry_macd_state="bullish"),
            _activity(instrument_id, _dt(1, 9, 5), "descending_triangle", 1.5),
            _outcome(instrument_id, _dt(1, 9, 5), "descending_triangle", direction="bear", pct_change_20=0.01,
                     entry_rsi_state="neutral"),
        ])
        session.commit()

    with session_factory() as session:
        cache = LibPatternOutcomes.InstrumentHistoryCache(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0),
        )
        cached_hammer_bands = cache.intensity_banded_analysis("hammer", bands=1)
        cached_hammer_states = cache.indicator_state_analysis("hammer")
        cached_hammer_pattern = cache.pattern_level_analysis("hammer")
        cached_triangle_pattern = cache.pattern_level_analysis("descending_triangle")

        direct_hammer_bands = LibPatternOutcomes.intensity_banded_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer", bands=1,
        )
        direct_hammer_states = LibPatternOutcomes.indicator_state_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer",
        )
        direct_hammer_pattern = LibPatternOutcomes.pattern_level_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer",
        )
        direct_triangle_pattern = LibPatternOutcomes.pattern_level_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "descending_triangle",
        )

    assert cached_hammer_bands == direct_hammer_bands
    assert cached_hammer_states == direct_hammer_states
    assert cached_hammer_pattern == direct_hammer_pattern
    assert cached_triangle_pattern == direct_triangle_pattern
    assert set(cache.patterns_in_scope()) == {"hammer", "descending_triangle"}


def test_instrument_history_cache_empty_pattern_returns_empty_not_error(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        cache = LibPatternOutcomes.InstrumentHistoryCache(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0),
        )
    assert cache.intensity_banded_analysis("hammer") == []
    assert cache.pattern_level_analysis("hammer") is None
    assert cache.band_outcomes("hammer") == []
    assert cache.indicator_state_analysis("hammer") == {
        "rsi": {"state": [], "trend": []}, "macd": {"state": [], "trend": []}, "stoch": {"state": [], "trend": []},
    }


def test_band_outcomes_matches_intensity_banded_analysis_boundaries(session_factory):
    """band_outcomes (added 2026-09-16 for guiding_scenarios.py's
    indicator-stat layer) must slice occurrences into the exact same bands
    intensity_banded_analysis itself reports stats for — same equal-count,
    lowest-intensity-first boundaries, just returning the raw PatternOutcome
    list per band instead of aggregated stats."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        for i, intensity in enumerate([1.0, 2.0, 3.0, 8.0, 9.0, 10.0]):
            ts = _dt(1 + i)
            session.add(_activity(instrument_id, ts, "hammer", intensity))
            session.add(_outcome(instrument_id, ts, "hammer", pct_change_20=0.01))
        session.commit()

    with session_factory() as session:
        cache = LibPatternOutcomes.InstrumentHistoryCache(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0),
        )
        stats = cache.intensity_banded_analysis("hammer", bands=2)
        outcomes = cache.band_outcomes("hammer", bands=2)

    assert len(stats) == len(outcomes) == 2
    for band_stats, band_outcomes in zip(stats, outcomes):
        assert band_stats["count"] == len(band_outcomes)
    # low band = the 3 lowest-intensity occurrences, high band = the 3 highest
    assert len(outcomes[0]) == 3
    assert len(outcomes[1]) == 3


# --------------------------------------------------------------------- indicator_state_analysis
# (state labels are now persisted directly on PatternOutcome — see
# pattern_outcome_analysis.py's own tests for the classification-at-
# analysis-time behavior; these tests just verify the grouping/ordering
# logic over whatever states are already stored.)

def test_indicator_state_analysis_groups_by_rsi_macd_stoch_state(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        # Occurrence 1: RSI oversold, MACD bearish, Stoch oversold — bull pattern that closed DOWN (not matched)
        session.add(_outcome(
            instrument_id, _dt(1, 9, 0), "hammer", direction="bull", pct_change_20=-0.01,
            entry_rsi=25.0, entry_rsi_state="oversold",
            entry_macd_line=0.5, entry_macd_signal=1.0, entry_macd_state="bearish",
            entry_stoch_k=15.0, entry_stoch_state="oversold",
        ))
        # Occurrence 2: RSI overbought, MACD bullish, Stoch overbought — bull pattern that closed UP (matched)
        session.add(_outcome(
            instrument_id, _dt(1, 9, 1), "hammer", direction="bull", pct_change_20=0.02,
            entry_rsi=75.0, entry_rsi_state="overbought",
            entry_macd_line=1.5, entry_macd_signal=1.0, entry_macd_state="bullish",
            entry_stoch_k=85.0, entry_stoch_state="overbought",
        ))
        session.commit()

    with session_factory() as session:
        result = LibPatternOutcomes.indicator_state_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer",
        )

    assert [b["label"] for b in result["rsi"]["state"]] == ["oversold", "overbought"]
    oversold, overbought = result["rsi"]["state"]
    assert oversold["count"] == 1 and oversold["checkpoints"][20]["matched"] == 0
    assert overbought["count"] == 1 and overbought["checkpoints"][20]["matched"] == 1

    assert [b["label"] for b in result["macd"]["state"]] == ["bearish", "bullish"]
    assert [b["label"] for b in result["stoch"]["state"]] == ["oversold", "overbought"]


def test_indicator_state_analysis_groups_by_trend(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_outcome(
            instrument_id, _dt(1, 9, 0), "hammer", direction="bull", pct_change_20=0.01,
            entry_rsi_trend="increasing", entry_macd_trend="decreasing", entry_stoch_trend="flat",
        ))
        session.add(_outcome(
            instrument_id, _dt(1, 9, 1), "hammer", direction="bull", pct_change_20=-0.01,
            entry_rsi_trend="decreasing", entry_macd_trend="increasing", entry_stoch_trend="flat",
        ))
        session.commit()

    with session_factory() as session:
        result = LibPatternOutcomes.indicator_state_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer",
        )

    # _TREND_ORDER = ("decreasing", "flat", "increasing")
    assert [b["label"] for b in result["rsi"]["trend"]] == ["decreasing", "increasing"]
    assert [b["label"] for b in result["macd"]["trend"]] == ["decreasing", "increasing"]
    assert [b["label"] for b in result["stoch"]["trend"]] == ["flat"]
    assert result["stoch"]["trend"][0]["count"] == 2


def test_indicator_state_analysis_skips_occurrences_with_no_state(session_factory):
    """A row with null entry_*_state/entry_*_trend (no CandleIndicators
    snapshot existed at analysis time, or the indicator was still warming
    up) should simply not count toward any indicator's bands, not error or
    force a fallback label."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_outcome(instrument_id, _dt(1, 9, 0), "hammer", pct_change_20=0.01))
        session.commit()

    with session_factory() as session:
        result = LibPatternOutcomes.indicator_state_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer",
        )
    empty = {"state": [], "trend": []}
    assert result == {"rsi": empty, "macd": empty, "stoch": empty}


def test_indicator_state_analysis_only_counts_the_requested_pattern(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_outcome(
            instrument_id, _dt(1, 9, 0), "hammer", pct_change_20=0.01, entry_rsi_state="oversold",
        ))
        session.add(_outcome(
            instrument_id, _dt(1, 9, 1), "doji", pct_change_20=0.01, entry_rsi_state="oversold",
        ))
        session.commit()

    with session_factory() as session:
        result = LibPatternOutcomes.indicator_state_analysis(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), "hammer",
        )
    assert result["rsi"]["state"][0]["count"] == 1
