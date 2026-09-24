from datetime import datetime, timedelta, timezone

from db.models import PatternOutcome, Strategy, SubscribedSymbol
from db.ops import LibStrategies
from strategy_generation import generate_best_patterns_strategy, rank_patterns

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


def _outcomes(instrument_id, pattern, win_count, total, direction="bull", offset=0):
    return [
        PatternOutcome(
            instrument_id=instrument_id, timeframe=TIMEFRAME, pattern=pattern, activity_type="candle_pattern",
            direction=direction, detected_ts=_dt(1 + offset + i), entry_price=100.0,
            pct_change_20=0.02 if i < win_count else -0.01, window_candles=30,
        )
        for i in range(total)
    ]


def test_rank_patterns_orders_by_wilson_score_and_excludes_small_samples(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        # strong: 30 occurrences, 27 wins (90%) -> high wilson score
        session.add_all(_outcomes(instrument_id, "hammer", 27, 30, offset=0))
        # decent: 15 occurrences, 11 wins (73%) -> lower wilson score than hammer
        session.add_all(_outcomes(instrument_id, "bullish_engulfing", 11, 15, offset=40))
        # too small to trust: 3 occurrences, 3 wins (100%) -> excluded entirely
        session.add_all(_outcomes(instrument_id, "morning_star", 3, 3, offset=60))
        session.commit()

    with session_factory() as session:
        ranked = rank_patterns(
            session, instrument_id, TIMEFRAME, _dt(90), datetime.now(timezone.utc), min_sample_size=10,
        )
    names = [r["pattern"] for r in ranked]
    assert names == ["hammer", "bullish_engulfing"]  # morning_star excluded, hammer ranks first
    assert ranked[0]["wilson_score"] > ranked[1]["wilson_score"]


def test_generate_best_patterns_strategy_creates_an_or_of_top_n(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all(_outcomes(instrument_id, "hammer", 27, 30, offset=0))
        session.add_all(_outcomes(instrument_id, "bullish_engulfing", 11, 15, offset=40))
        session.add_all(_outcomes(instrument_id, "macd_bullish_cross", 10, 15, offset=70, direction="bull"))
        session.commit()

    result = generate_best_patterns_strategy(
        session_factory, instrument_id, TIMEFRAME, "RS1_best_patterns_reliance", top_n=2, min_sample_size=10,
    )
    assert result["action"] == "created"
    assert result["pattern_filter"] == "hammer,bullish_engulfing"  # top 2 only, not all 3
    assert len(result["selected_patterns"]) == 2

    with session_factory() as session:
        strategy = LibStrategies.get_by_id(session, result["strategy_id"])
    assert strategy.name == "RS1_best_patterns_reliance"
    assert strategy.strategy_type == "RS1_best_patterns"
    assert strategy.pattern_filter == "hammer,bullish_engulfing"
    assert strategy.direction == "both"


def test_generate_best_patterns_strategy_updates_in_place_on_rerun(session_factory):
    """The whole point: "it should be updated after every backtest...
    weekly." Re-running with different underlying data must update the
    SAME strategy row, not create a duplicate."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all(_outcomes(instrument_id, "hammer", 27, 30, offset=0))
        session.commit()

    first = generate_best_patterns_strategy(
        session_factory, instrument_id, TIMEFRAME, "RS1_best_patterns_reliance", top_n=2, min_sample_size=10,
    )
    assert first["action"] == "created"
    assert first["pattern_filter"] == "hammer"

    # A week later: a new, even-better pattern now has enough history too.
    with session_factory() as session:
        session.add_all(_outcomes(instrument_id, "macd_bullish_cross", 29, 30, offset=100))
        session.commit()

    second = generate_best_patterns_strategy(
        session_factory, instrument_id, TIMEFRAME, "RS1_best_patterns_reliance", top_n=2, min_sample_size=10,
    )
    assert second["action"] == "updated"
    assert second["strategy_id"] == first["strategy_id"]  # same row, not a new one
    assert "macd_bullish_cross" in second["pattern_filter"]

    with session_factory() as session:
        count = session.query(Strategy).filter_by(name="RS1_best_patterns_reliance").count()
    assert count == 1  # never duplicated


def test_generate_best_patterns_strategy_never_pads_with_weak_patterns(session_factory):
    """top_n=5 requested but only 1 pattern actually clears min_sample_size
    -> pattern_filter has just that 1, never padded to hit the count."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all(_outcomes(instrument_id, "hammer", 27, 30, offset=0))
        session.commit()

    result = generate_best_patterns_strategy(
        session_factory, instrument_id, TIMEFRAME, "RS1_best_patterns_reliance", top_n=5, min_sample_size=10,
    )
    assert result["pattern_filter"] == "hammer"
    assert len(result["selected_patterns"]) == 1
