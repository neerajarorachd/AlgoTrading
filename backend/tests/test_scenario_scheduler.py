from datetime import datetime, timedelta, timezone

from db.models import InstrumentActivity, PatternOutcome, SubscribedSymbol
from scenario_scheduler import _next_weekly_target, run_guiding_scenario_regeneration


def _register(session_factory, symbol="RELIANCE") -> int:
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id=f"SEC-{symbol}", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _dt(days_ago):
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def test_next_weekly_target_rolls_forward_within_the_week():
    now = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)  # a Wednesday
    target = _next_weekly_target(now, day_of_week=6, hour_utc=2, minute_utc=0)  # Sunday
    assert target.weekday() == 6
    assert target > now
    assert (target - now).days == 3  # Wed -> Sun


def test_next_weekly_target_rolls_to_next_week_once_past_the_time():
    now = datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc)  # Sunday, 05:00, already past 02:00
    target = _next_weekly_target(now, day_of_week=6, hour_utc=2, minute_utc=0)
    assert target.weekday() == 6
    assert (target - now).days == 6


def test_run_guiding_scenario_regeneration_covers_every_active_symbol_and_window(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        # 24 occurrences so the default 3-way intensity banding (ceil(24/3)=8
        # per band) leaves each band >= the min_sample_size=7 floor -- the
        # lowest-intensity band (indices 0-7) is all wins, clearing 70%.
        for i in range(24):
            ts = _dt(1 + i)
            session.add(InstrumentActivity(
                instrument_id=instrument_id, timeframe="1min", ts=ts, activity_type="candle_pattern",
                activity="hammer", intensity=1.0 + i * 0.01,
                open_price=100, high_price=101, low_price=99, close_price=100.5,
            ))
            session.add(PatternOutcome(
                instrument_id=instrument_id, timeframe="1min", pattern="hammer", activity_type="candle_pattern",
                direction="bull", detected_ts=ts, entry_price=100.0,
                pct_change_20=0.02 if i < 8 else -0.01, window_candles=30,
            ))
        session.commit()

    result = run_guiding_scenario_regeneration(session_factory)
    assert result["symbols_scanned"] == 1
    assert result["errors"] == []
    # only "1min" has real data, but every timeframe x window combo is still
    # attempted (no error) -- the ones with no data just write 0 scenarios
    assert result["scenarios_written"] >= 1  # the real hammer band clears 70%/n>=7


def test_run_guiding_scenario_regeneration_skips_nothing_on_an_empty_symbol(session_factory):
    """A freshly-registered symbol with no PatternOutcome data yet must not
    error out the whole pass -- just contribute 0 scenarios."""
    _register(session_factory, "TCS")
    result = run_guiding_scenario_regeneration(session_factory)
    assert result["symbols_scanned"] == 1
    assert result["errors"] == []
    assert result["scenarios_written"] == 0
