from datetime import datetime, timedelta, timezone

import pytest

from db.models import GuidingScenario, GuidingScenarioIndicatorStat, InstrumentActivity, PatternOutcome, SubscribedSymbol
from db.ops import LibSystemSettings
from guiding_scenarios import generate_guiding_scenarios, match_guiding_scenario
import recommendation_engine

SYMBOL = "RELIANCE"
TIMEFRAME = "1min"


def _dt(days_ago):
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _register(session_factory) -> int:
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id="1333", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _activity(instrument_id, ts, pattern, intensity):
    return InstrumentActivity(
        instrument_id=instrument_id, timeframe=TIMEFRAME, ts=ts, activity_type="candle_pattern",
        activity=pattern, intensity=intensity, open_price=100, high_price=101, low_price=99, close_price=100.5,
    )


def _outcome(instrument_id, ts, pattern, direction, pct20, **indicator_kwargs):
    return PatternOutcome(
        instrument_id=instrument_id, timeframe=TIMEFRAME, pattern=pattern, activity_type="candle_pattern",
        direction=direction, detected_ts=ts, entry_price=100.0, pct_change_20=pct20, window_candles=30,
        **indicator_kwargs,
    )


def _raw_scenario(instrument_id, pattern, window_kind, band_min=0.5, band_max=1.5):
    return GuidingScenario(
        instrument_id=instrument_id, timeframe=TIMEFRAME, pattern=pattern, window_kind=window_kind,
        direction="bull", band_min=band_min, band_max=band_max, checkpoint=20,
        sample_count=10, win_count=8, win_pct=0.8, wilson_score=0.5,
        win_pct_threshold_applied=0.70, min_sample_size_applied=10,
    )


def test_generate_guiding_scenarios_writes_only_qualifying_bands(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        # low-intensity band: 10 occurrences, 8 wins (80%) -> qualifies (>=70%, n>=10)
        for i in range(10):
            ts = _dt(1 + i)
            session.add(_activity(instrument_id, ts, "hammer", 1.0 + i * 0.01))
            session.add(_outcome(instrument_id, ts, "hammer", "bull", 0.02 if i < 8 else -0.01))
        # high-intensity band: 10 occurrences, 3 wins (30%) -> does NOT qualify
        for i in range(10):
            ts = _dt(30 + i)
            session.add(_activity(instrument_id, ts, "hammer", 10.0 + i * 0.01))
            session.add(_outcome(instrument_id, ts, "hammer", "bull", 0.02 if i < 3 else -0.01))
        session.commit()

    result = generate_guiding_scenarios(
        session_factory, instrument_id, TIMEFRAME, "2y", bands=2,
        min_sample_size=10, win_pct_threshold=0.70, wilson_confidence=0.95,
    )
    assert result["scenarios_written"] == 1
    assert result["patterns_scanned"] == 1
    assert result["window_kind"] == "2y"

    with session_factory() as session:
        scenarios = session.query(GuidingScenario).filter_by(instrument_id=instrument_id).all()
    assert len(scenarios) == 1
    assert scenarios[0].win_count == 8
    assert scenarios[0].sample_count == 10
    assert scenarios[0].window_kind == "2y"
    assert float(scenarios[0].band_min) < float(scenarios[0].band_max) <= 2.0  # the low-intensity band, not the high one


def test_generate_guiding_scenarios_is_idempotent_on_rerun(session_factory):
    """The whole regeneration model: delete-and-bulk-reinsert per
    (instrument, timeframe, window_kind), never accumulates duplicates
    across repeated weekly runs."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        for i in range(10):
            ts = _dt(1 + i)
            session.add(_activity(instrument_id, ts, "hammer", 1.0 + i * 0.01))
            session.add(_outcome(instrument_id, ts, "hammer", "bull", 0.02 if i < 8 else -0.01))
        session.commit()

    kwargs = dict(bands=1, min_sample_size=10, win_pct_threshold=0.70, wilson_confidence=0.95)
    generate_guiding_scenarios(session_factory, instrument_id, TIMEFRAME, "2y", **kwargs)
    generate_guiding_scenarios(session_factory, instrument_id, TIMEFRAME, "2y", **kwargs)

    with session_factory() as session:
        scenarios = session.query(GuidingScenario).filter_by(instrument_id=instrument_id).all()
    assert len(scenarios) == 1


def test_generate_guiding_scenarios_only_replaces_its_own_window_kind(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_raw_scenario(instrument_id, "hammer", "3m"))
        for i in range(10):
            ts = _dt(1 + i)
            session.add(_activity(instrument_id, ts, "hammer", 1.0 + i * 0.01))
            session.add(_outcome(instrument_id, ts, "hammer", "bull", 0.02 if i < 8 else -0.01))
        session.commit()

    generate_guiding_scenarios(
        session_factory, instrument_id, TIMEFRAME, "2y",
        bands=1, min_sample_size=10, win_pct_threshold=0.70, wilson_confidence=0.95,
    )

    with session_factory() as session:
        scenarios = session.query(GuidingScenario).filter_by(instrument_id=instrument_id).all()
    kinds = {s.window_kind for s in scenarios}
    assert kinds == {"2y", "3m"}  # the pre-existing 3m row is untouched


def test_generate_guiding_scenarios_writes_indicator_stats_scoped_to_the_band(session_factory):
    """The informational layer — computed over ONLY this band's own
    occurrence subset, never gating the scenario itself."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        for i in range(10):
            ts = _dt(1 + i)
            session.add(_activity(instrument_id, ts, "hammer", 1.0 + i * 0.01))
            session.add(_outcome(
                instrument_id, ts, "hammer", "bull", 0.02 if i < 8 else -0.01,
                entry_rsi_state="neutral" if i < 5 else "overbought",
            ))
        session.commit()

    result = generate_guiding_scenarios(
        session_factory, instrument_id, TIMEFRAME, "2y",
        bands=1, min_sample_size=10, win_pct_threshold=0.70, wilson_confidence=0.95,
    )
    assert result["scenarios_written"] == 1
    assert result["indicator_stats_written"] > 0

    with session_factory() as session:
        scenario = session.query(GuidingScenario).filter_by(instrument_id=instrument_id).one()
        stats = session.query(GuidingScenarioIndicatorStat).filter_by(guiding_scenario_id=scenario.id).all()
    rsi_state_labels = {s.label for s in stats if s.dimension == "rsi_state"}
    assert rsi_state_labels == {"neutral", "overbought"}


def test_generate_guiding_scenarios_rescues_a_band_that_only_qualifies_at_a_different_checkpoint(session_factory):
    """"review internal threshold data... if there are 50% acceptance,
    don't discard it" (2026-09-16, real user feedback after the first run
    only found 8 scenarios for HINDCOPPER): a band that misses the bar at
    one checkpoint can still be a genuine 70%+ scenario at another
    checkpoint entirely — must not be silently thrown away just because
    checkpoint 20 alone used to decide everything."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        for i in range(10):
            ts = _dt(1 + i)
            session.add(_activity(instrument_id, ts, "hammer", 1.0 + i * 0.01))
            # a real, plausible shape: strong short-term pop (8/10 win at 5
            # candles = 80%) that fades by 20 candles (only 3/10 still up =
            # 30%) — not a data artifact, a pattern that's only good for a
            # short holding period.
            session.add(PatternOutcome(
                instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
                activity_type="candle_pattern", direction="bull", detected_ts=ts,
                entry_price=100.0, window_candles=30,
                pct_change_5=0.01 if i < 8 else -0.01,
                pct_change_20=0.01 if i < 3 else -0.01,
            ))
        session.commit()

    result = generate_guiding_scenarios(
        session_factory, instrument_id, TIMEFRAME, "2y",
        bands=1, min_sample_size=7, win_pct_threshold=0.70, wilson_confidence=0.95,
    )
    assert result["scenarios_written"] == 1  # would have been 0 under the old single-checkpoint (20) gate
    with session_factory() as session:
        scenario = session.query(GuidingScenario).filter_by(instrument_id=instrument_id).one()
    assert scenario.checkpoint == 5  # rescued via the 5-candle checkpoint, not 20
    assert scenario.win_count == 8
    assert scenario.sample_count == 10


def test_unknown_window_kind_raises(session_factory):
    instrument_id = _register(session_factory)
    with pytest.raises(ValueError):
        generate_guiding_scenarios(session_factory, instrument_id, TIMEFRAME, "5y")


def test_match_guiding_scenario_finds_containing_band_and_rejects_out_of_range(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_raw_scenario(instrument_id, "hammer", "2y", band_min=0.5, band_max=1.5))
        session.commit()

    with session_factory() as session:
        match = match_guiding_scenario(session, instrument_id, TIMEFRAME, "hammer", "2y", 1.0)
        assert match is not None
        assert match.win_count == 8

        no_match = match_guiding_scenario(session, instrument_id, TIMEFRAME, "hammer", "2y", 5.0)
        assert no_match is None


def test_generate_guiding_scenarios_resolves_defaults_from_system_settings(session_factory):
    """No explicit checkpoint/min_sample_size/win_pct_threshold/
    wilson_confidence passed — must fall back to the SAME SystemSetting
    keys recommendation_engine.py already seeds, no separately-invented
    settings for this module."""
    LibSystemSettings.seed_defaults(session_factory, recommendation_engine.SYSTEM_SETTING_SEED)
    instrument_id = _register(session_factory)
    with session_factory() as session:
        for i in range(10):
            ts = _dt(1 + i)
            session.add(_activity(instrument_id, ts, "hammer", 1.0 + i * 0.01))
            session.add(_outcome(instrument_id, ts, "hammer", "bull", 0.02 if i < 8 else -0.01))
        session.commit()

    result = generate_guiding_scenarios(session_factory, instrument_id, TIMEFRAME, "2y", bands=1)
    assert result["scenarios_written"] == 1  # 80% clears the seeded 70% default with n=10>=10
