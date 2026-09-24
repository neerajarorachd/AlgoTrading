from datetime import datetime, timedelta, timezone

import recommendation_engine as re_mod
import recommendation_outcomes as ro
from db.models import (
    CandleHistorical, CandleToday, GuidingScenario, Recommendation, RecommendationOutcome, SubscribedSymbol,
)
from db.ops import LibSystemSettings
from stats_utils import wilson_lower_bound

T0 = datetime(2026, 9, 18, 4, 0)  # naive UTC, like every DB datetime here
NOW = datetime(2026, 9, 18, 5, 0, tzinfo=timezone.utc)


def _setup(session_factory, checkpoint=5):
    LibSystemSettings.seed_defaults(session_factory, re_mod.SYSTEM_SETTING_SEED)
    with session_factory() as session:
        inst = SubscribedSymbol(symbol="RELIANCE", exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
                                security_id="S1", previous_close=100.0)
        session.add(inst)
        session.add(GuidingScenario(
            instrument_id=1, timeframe="3min", pattern="hammer", window_kind="2y", direction="bull",
            band_min=0.5, band_max=1.5, checkpoint=checkpoint, sample_count=10, win_count=8, win_pct=0.8,
            wilson_score=wilson_lower_bound(8, 10, 0.95), win_pct_threshold_applied=0.7,
            min_sample_size_applied=10,
        ))
        session.commit()
        return inst.id


def _recommend(session_factory, inst, direction="bull", pattern="hammer", gate=None):
    return re_mod.generate_recommendations(
        session_factory, instrument_id=inst, timeframe="3min", pattern=pattern, direction=direction,
        entry_price=100.0, detected_ts=T0, intensity=1.0, rule_gate=gate,
    )["id"]


def _candles(session_factory, closes, model=CandleToday):
    with session_factory() as session:
        for i, close in enumerate(closes, start=1):
            session.add(model(
                symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="3min", ts=T0 + timedelta(minutes=3 * i),
                open_price=close, high_price=close + 0.5, low_price=close - 0.5, close_price=close, volume=1,
            ))
        session.commit()


def _outcome(session_factory, rec_id):
    with session_factory() as session:
        return session.query(RecommendationOutcome).filter_by(recommendation_id=rec_id).one_or_none()


def test_bull_recommendation_wins_when_price_is_up_at_its_checkpoint(session_factory):
    inst = _setup(session_factory)
    rec = _recommend(session_factory, inst)
    _candles(session_factory, [100.2, 100.4, 100.6, 100.8, 101.0])  # 5 candles, checkpoint 5 -> +1%
    assert ro.resolve_outcomes(session_factory, NOW) == 1
    o = _outcome(session_factory, rec)
    assert o.win is True and o.candles_observed == 5 and o.status == "partial"  # 30 not yet seen
    assert abs(float(o.checkpoint_pct) - 0.01) < 1e-6
    assert float(o.max_favorable_pct) > 0.01 and o.pct_change_10 is None


def test_bear_recommendation_wins_when_price_falls(session_factory):
    inst = _setup(session_factory)
    rec = _recommend(session_factory, inst, direction="bear")
    _candles(session_factory, [99.9, 99.8, 99.6, 99.4, 99.0])
    ro.resolve_outcomes(session_factory, NOW)
    assert _outcome(session_factory, rec).win is True


def test_win_is_unknown_until_the_checkpoint_candles_exist(session_factory):
    inst = _setup(session_factory)
    rec = _recommend(session_factory, inst)
    _candles(session_factory, [100.5, 100.6])  # only 2 of 5
    ro.resolve_outcomes(session_factory, NOW)
    o = _outcome(session_factory, rec)
    assert o.win is None and o.checkpoint_pct is None and o.candles_observed == 2


def test_partial_row_is_updated_in_place_then_completes_when_a_day_old(session_factory):
    inst = _setup(session_factory)
    rec = _recommend(session_factory, inst)
    _candles(session_factory, [100.5])
    ro.resolve_outcomes(session_factory, NOW)
    first = _outcome(session_factory, rec)
    assert first.win is None and first.status == "partial"

    with session_factory() as session:  # add candles 2..5 so the checkpoint exists
        for i, close in enumerate([100.6, 100.7, 100.8, 100.9], start=2):
            session.add(CandleToday(symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="3min",
                                    ts=T0 + timedelta(minutes=3 * i), open_price=close, high_price=close,
                                    low_price=close, close_price=close, volume=1))
        session.commit()
    ro.resolve_outcomes(session_factory, NOW)
    assert _outcome(session_factory, rec).win is True

    later = NOW + timedelta(days=2)  # a day+ old: no more data is coming -> complete
    ro.resolve_outcomes(session_factory, later)
    with session_factory() as session:
        rows = session.query(RecommendationOutcome).filter_by(recommendation_id=rec).all()
    assert len(rows) == 1 and rows[0].status == "complete"


def test_rejected_recommendations_are_tracked_too(session_factory):
    inst = _setup(session_factory)
    rec = _recommend(session_factory, inst, gate=lambda: (False, "rules failed: R1"))
    _candles(session_factory, [100.2, 100.4, 100.6, 100.8, 101.0])
    ro.resolve_outcomes(session_factory, NOW)
    with session_factory() as session:
        assert session.query(Recommendation).filter_by(id=rec).one().status == "rejected"
    assert _outcome(session_factory, rec).win is True  # a rejected winner: a false negative


def test_candles_fall_back_to_history_after_the_daily_archive(session_factory):
    inst = _setup(session_factory)
    rec = _recommend(session_factory, inst)
    _candles(session_factory, [100.2, 100.4, 100.6, 100.8, 101.0], model=CandleHistorical)
    ro.resolve_outcomes(session_factory, NOW)
    assert _outcome(session_factory, rec).win is True


def test_complete_outcomes_are_not_recomputed(session_factory):
    inst = _setup(session_factory)
    rec = _recommend(session_factory, inst)
    _candles(session_factory, [100.0 + 0.1 * i for i in range(1, 31)])  # all 30 -> complete at once
    assert ro.resolve_outcomes(session_factory, NOW) == 1
    assert _outcome(session_factory, rec).status == "complete"
    assert ro.resolve_outcomes(session_factory, NOW) == 0


def test_pattern_summary_separates_recommended_from_rule_rejected(session_factory):
    inst = _setup(session_factory)
    _recommend(session_factory, inst)  # recommended, will win
    with session_factory() as session:
        session.add(GuidingScenario(
            instrument_id=inst, timeframe="3min", pattern="doji", window_kind="2y", direction="bull",
            band_min=0.5, band_max=1.5, checkpoint=5, sample_count=10, win_count=8, win_pct=0.8,
            wilson_score=wilson_lower_bound(8, 10, 0.95), win_pct_threshold_applied=0.7,
            min_sample_size_applied=10))
        session.commit()
    _recommend(session_factory, inst, pattern="doji", gate=lambda: (False, "rules failed: R1"))
    _candles(session_factory, [100.2, 100.4, 100.6, 100.8, 101.0])
    ro.resolve_outcomes(session_factory, NOW)

    with session_factory() as session:
        rows = {r["pattern"]: r for r in ro.pattern_summary(session)}
    assert rows["hammer"]["recommended"] == 1 and rows["hammer"]["win_pct"] == 1.0
    assert rows["doji"]["rejected"] == 1 and rows["doji"]["rule_rejected"] == 1
    assert rows["doji"]["rejected_win_pct"] == 1.0 and rows["doji"]["win_pct"] is None


def test_history_endpoint_filters_by_pattern_and_carries_the_outcome(client, session_factory):
    inst = _setup(session_factory)
    _recommend(session_factory, inst)
    _candles(session_factory, [100.2, 100.4, 100.6, 100.8, 101.0])
    ro.resolve_outcomes(session_factory, NOW)

    rows = client.get("/api/recommendations/history?pattern=hammer").get_json()
    assert len(rows) == 1 and rows[0]["outcome"]["win"] is True
    assert client.get("/api/recommendations/history?pattern=nope").get_json() == []
    summary = client.get("/api/recommendations/pattern-summary").get_json()
    assert summary[0]["pattern"] == "hammer" and summary[0]["win_pct"] == 1.0
