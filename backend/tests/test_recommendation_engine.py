from datetime import datetime, timedelta, timezone

from db.models import GuidingScenario, Recommendation, RecommendationSystem, SubscribedSymbol
from db.ops import LibRecommendationSystems, LibRecommendations, LibSystemSettings
from stats_utils import wilson_lower_bound
import recommendation_engine as re_mod

SYMBOL = "RELIANCE"
TIMEFRAME = "3min"


def _dt(days_ago):
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _register(session_factory, symbol=SYMBOL) -> int:
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id=f"SEC-{symbol}", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _scenario(instrument_id, pattern, window_kind, band_min, band_max, sample_count, win_count, checkpoint=20):
    """A precomputed GuidingScenario row, built directly (bypassing
    guiding_scenarios.generate_guiding_scenarios — that generation
    algorithm has its own dedicated test file, test_guiding_scenarios.py)
    so these tests focus purely on generate_recommendations' LOOKUP
    behavior against an already-qualified scenario."""
    win_pct = win_count / sample_count
    return GuidingScenario(
        instrument_id=instrument_id, timeframe=TIMEFRAME, pattern=pattern, window_kind=window_kind,
        direction="bull", band_min=band_min, band_max=band_max, checkpoint=checkpoint,
        sample_count=sample_count, win_count=win_count, win_pct=win_pct,
        wilson_score=wilson_lower_bound(win_count, sample_count, 0.95),
        win_pct_threshold_applied=0.70, min_sample_size_applied=10,
    )


def _seed_settings(session_factory):
    LibSystemSettings.seed_defaults(session_factory, re_mod.SYSTEM_SETTING_SEED)


def test_recommendation_system_seed_creates_rs1(session_factory):
    LibRecommendationSystems.seed_defaults(session_factory, re_mod.RECOMMENDATION_SYSTEM_SEED)
    with session_factory() as session:
        rs1 = LibRecommendationSystems.get_by_code(session, "RS1")
    assert rs1 is not None
    assert rs1.kind == "system"
    assert rs1.top_n_per_run == 3
    assert rs1.analysis_mode == "primary_confirmation"


def test_recommendation_system_seed_no_longer_creates_rs2_or_rs3(session_factory):
    """RS2/RS3 (real, built and tested 2026-09-15) were removed 2026-09-16
    once RS1's own mechanism was redesigned around precomputed guiding
    scenarios — only RS1 exists now."""
    LibRecommendationSystems.seed_defaults(session_factory, re_mod.RECOMMENDATION_SYSTEM_SEED)
    with session_factory() as session:
        assert LibRecommendationSystems.get_by_code(session, "RS2") is None
        assert LibRecommendationSystems.get_by_code(session, "RS3") is None


def test_no_intensity_returns_none(session_factory):
    """Pattern + intensity band are BOTH required by the guiding-scenario
    decision gate — a live signal with no intensity value can't be
    evaluated by this mechanism at all, no whole-pattern fallback anymore."""
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    result = re_mod.generate_recommendations(
        session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
        direction="bull", entry_price=101.0, detected_ts=_dt(0), intensity=None,
    )
    assert result is None


def test_no_matching_guiding_scenario_returns_none(session_factory):
    """Nothing precomputed for this pattern/window at all — a clean, fast
    "pass," not an error, and nothing gets persisted (no "rejected" audit
    row is possible anymore — see generate_recommendations's own docstring)."""
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    result = re_mod.generate_recommendations(
        session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
        direction="bull", entry_price=101.0, detected_ts=_dt(0), intensity=1.0,
    )
    assert result is None
    with session_factory() as session:
        assert LibRecommendations.get_pending(session, instrument_id, TIMEFRAME) == []


def test_generate_recommendations_queues_on_a_matching_scenario(session_factory):
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_scenario(instrument_id, "hammer", "2y", 0.5, 1.5, sample_count=10, win_count=8))
        session.commit()

    result = re_mod.generate_recommendations(
        session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
        direction="bull", entry_price=101.0, detected_ts=_dt(0), intensity=1.0,
    )
    assert result is not None
    assert result["status"] == "queued"
    assert result["band_kind"] == "intensity"

    with session_factory() as session:
        row = session.query(Recommendation).filter_by(id=result["id"]).one()
    assert row.sample_count == 10
    assert row.win_count == 8
    assert float(row.win_pct) == 0.8
    assert row.guiding_scenario_id is not None
    assert row.window_kind == "2y"
    assert row.veto_reason is None
    assert row.confirmations_checked == 0
    assert row.confirmations_passed == 0


def test_intensity_outside_every_stored_band_returns_none(session_factory):
    """No nearest-edge fallback — a deliberate change from the old live
    RS1's _find_intensity_band, which stretched to the closest band for an
    out-of-range live intensity. A precomputed guiding scenario is a
    specific, already-decided claim ("this exact range has historically
    won"), not a live approximation to stretch to fit."""
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_scenario(instrument_id, "hammer", "2y", 0.5, 1.5, sample_count=10, win_count=8))
        session.commit()

    result = re_mod.generate_recommendations(
        session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
        direction="bull", entry_price=101.0, detected_ts=_dt(0), intensity=5.0,
    )
    assert result is None


def test_window_kind_is_scoped_independently(session_factory):
    """A scenario stored under "3m" must not be matched when the caller
    asks for "2y" — the two windows are independent, no combination rule
    (explicit product decision, 2026-09-16: "store both, decide later")."""
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_scenario(instrument_id, "hammer", "3m", 0.5, 1.5, sample_count=10, win_count=8))
        session.commit()

    via_2y = re_mod.generate_recommendations(
        session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
        direction="bull", entry_price=101.0, detected_ts=_dt(0), intensity=1.0, window_kind="2y",
    )
    assert via_2y is None

    via_3m = re_mod.generate_recommendations(
        session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
        direction="bull", entry_price=101.0, detected_ts=_dt(0) - timedelta(minutes=1),
        intensity=1.0, window_kind="3m",
    )
    assert via_3m is not None and via_3m["status"] == "queued"


def test_indicator_state_never_gates_the_decision(session_factory):
    """Explicit product decision, 2026-09-16: indicator state/trend must
    never interfere in the take-call/pass decision — a live signal with a
    historically-terrible rsi_state still queues as long as pattern +
    intensity band match; the value is only stored as signal context."""
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_scenario(instrument_id, "hammer", "2y", 0.5, 1.5, sample_count=10, win_count=8))
        session.commit()

    result = re_mod.generate_recommendations(
        session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
        direction="bull", entry_price=101.0, detected_ts=_dt(0), intensity=1.0,
        rsi_state="oversold", rsi_value=12.0,
    )
    assert result["status"] == "queued"
    with session_factory() as session:
        row = session.query(Recommendation).filter_by(id=result["id"]).one()
    assert row.signal_rsi_state == "oversold"  # stored for context, never gated on
    assert float(row.signal_rsi) == 12.0
    assert row.veto_reason is None


def test_raw_indicator_values_are_persisted_alongside_state_labels(session_factory):
    """Real gap found 2026-09-15: only state/trend LABELS were stored on
    Recommendation, never the raw numbers — unlike PatternOutcome, which
    already stores both. Still true after the guiding-scenarios redesign."""
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_scenario(instrument_id, "hammer", "2y", 0.5, 1.5, sample_count=10, win_count=8))
        session.commit()

    result = re_mod.generate_recommendations(
        session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
        direction="bull", entry_price=101.0, detected_ts=_dt(0), intensity=1.0,
        rsi_value=70.12, macd_line=-0.0741, macd_signal=-0.1654, stoch_value=38.85,
    )
    with session_factory() as session:
        row = session.query(Recommendation).filter_by(id=result["id"]).one()
    assert float(row.signal_rsi) == 70.12
    assert float(row.signal_macd_line) == -0.0741
    assert float(row.signal_macd_signal) == -0.1654
    assert float(row.signal_stoch_k) == 38.85


def test_two_different_rs_can_each_get_their_own_row_for_the_identical_signal(session_factory):
    """Regression guard for the recommendation_system_id unique-constraint
    fix (real bug found live, 2026-09-15: two RS's sharing an engine
    collided on an identical live signal). Still a real risk the moment a
    second RS row exists, even after RS2/RS3's own removal — rewritten
    against two generic RS rows rather than the now-gone RS1+RS3 pair."""
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    with session_factory() as session:
        rs_a = RecommendationSystem(code="RS-A", name="Test RS A", timeframe=TIMEFRAME)
        rs_b = RecommendationSystem(code="RS-B", name="Test RS B", timeframe=TIMEFRAME)
        session.add_all([rs_a, rs_b])
        session.add(_scenario(instrument_id, "hammer", "2y", 0.5, 1.5, sample_count=10, win_count=8))
        session.commit()
        session.refresh(rs_a)
        session.refresh(rs_b)
        rs_a_id, rs_b_id = rs_a.id, rs_b.id

    same_ts = _dt(0)
    via_a = re_mod.generate_recommendations(
        session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
        direction="bull", entry_price=101.0, detected_ts=same_ts, intensity=1.0,
        recommendation_system_id=rs_a_id,
    )
    via_b = re_mod.generate_recommendations(
        session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
        direction="bull", entry_price=101.0, detected_ts=same_ts, intensity=1.0,
        recommendation_system_id=rs_b_id,
    )
    assert via_a is not None and via_a["status"] == "queued"
    assert via_b is not None and via_b["status"] == "queued"

    with session_factory() as session:
        rows = (
            session.query(Recommendation)
            .filter_by(instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer", detected_ts=same_ts)
            .all()
        )
    assert {r.recommendation_system_id for r in rows} == {rs_a_id, rs_b_id}


def test_sweep_and_regenerate_threads_recommendation_system_id_and_window_kind(session_factory):
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    with session_factory() as session:
        rs = RecommendationSystem(code="RS-TEST2", name="Test RS 2", timeframe=TIMEFRAME)
        session.add(rs)
        session.add(_scenario(instrument_id, "hammer", "2y", 0.5, 1.5, sample_count=10, win_count=8))
        session.commit()
        session.refresh(rs)
        rs_id = rs.id

    re_mod.generate_recommendations(
        session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
        direction="bull", entry_price=101.0, detected_ts=_dt(0), intensity=1.0,
        recommendation_system_id=rs_id,
    )

    with session_factory() as session:
        row = LibRecommendations.get_pending(session, instrument_id, TIMEFRAME)[0]
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.commit()

    result = re_mod.sweep_and_regenerate(session_factory)
    assert result["expired"] == 1
    assert result["regenerated"] == 1

    with session_factory() as session:
        pending = LibRecommendations.get_pending(session, instrument_id, TIMEFRAME)
    assert len(pending) == 1
    child = pending[0]
    assert child.recommendation_system_id == rs_id
    assert child.regeneration_count == 1
    assert child.window_kind == "2y"


def test_generate_recommendations_batch_matches_individual_calls(session_factory):
    """Same lookup, same results whether called individually or through
    the batch path — the batch path's own value now is bulk-inserting via
    create_bulk instead of one create_one call per signal, not a query-
    count reduction (each guiding-scenario lookup is already cheap)."""
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_scenario(instrument_id, "hammer", "2y", 0.5, 1.5, sample_count=10, win_count=8))
        session.commit()

    batch_signals = [
        {"pattern": "hammer", "direction": "bull", "entry_price": 101.0, "detected_ts": _dt(0), "intensity": 1.0},
        {"pattern": "shooting_star", "direction": "bear", "entry_price": 102.0, "detected_ts": _dt(0) - timedelta(minutes=1), "intensity": 1.0},
    ]
    batch_results = re_mod.generate_recommendations_batch(session_factory, instrument_id, TIMEFRAME, batch_signals)

    with session_factory() as session:
        session.query(Recommendation).delete(synchronize_session=False)
        session.commit()

    individual_results = [
        re_mod.generate_recommendations(
            session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="hammer",
            direction="bull", entry_price=101.0, detected_ts=_dt(0), intensity=1.0,
        ),
        re_mod.generate_recommendations(
            session_factory, instrument_id=instrument_id, timeframe=TIMEFRAME, pattern="shooting_star",
            direction="bear", entry_price=102.0, detected_ts=_dt(0) - timedelta(minutes=1), intensity=1.0,
        ),
    ]

    assert len(batch_results) == 2
    for batch_r, individual_r in zip(batch_results, individual_results):
        assert (batch_r is None) == (individual_r is None)
        if batch_r is not None:
            assert batch_r["status"] == individual_r["status"]
            assert batch_r["band_kind"] == individual_r["band_kind"]
            assert batch_r["wilson_score"] == individual_r["wilson_score"]

    assert batch_results[0]["status"] == "queued"
    assert batch_results[1] is None  # no guiding scenario ever stored for shooting_star


def test_on_activities_queues_from_a_candle_closed_activity_list(session_factory):
    """The live call-site adapter — mirrors PredictionTracker.on_activities'
    own signature exactly, taking whatever ActivityEngine.on_candle_closed
    just returned. First real live consumer of that return value, wired
    into app.py's _make_on_candle_closed 2026-09-16."""
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_scenario(instrument_id, "hammer", "2y", 0.5, 1.5, sample_count=10, win_count=8))
        session.commit()

    activities = [{
        "instrument_id": instrument_id, "timeframe": TIMEFRAME, "ts": _dt(0),
        "activity_type": "candle_pattern", "activity": "hammer", "intensity": 1.0,
        "open_price": 100, "high_price": 101, "low_price": 99, "close_price": 101.0,
    }]
    queued = re_mod.on_activities(session_factory, SYMBOL, "NSE_EQ", activities)
    assert len(queued) == 1
    assert queued[0]["status"] == "queued"
    with session_factory() as session:
        pending = LibRecommendations.get_pending(session, instrument_id, TIMEFRAME)
    assert len(pending) == 1
    assert pending[0].id == queued[0]["id"]
    assert pending[0].direction == "bull"  # derived from BULLISH_PATTERNS, no direction field on the activity dict


def test_on_activities_skips_untracked_patterns_and_missing_intensity(session_factory):
    _seed_settings(session_factory)
    instrument_id = _register(session_factory)
    activities = [
        {  # rectangle: no directional bias, not in BULLISH_PATTERNS/BEARISH_PATTERNS
            "instrument_id": instrument_id, "timeframe": TIMEFRAME, "ts": _dt(0),
            "activity_type": "graph_formation", "activity": "rectangle", "intensity": 1.0,
            "open_price": 100, "high_price": 101, "low_price": 99, "close_price": 101.0,
        },
        {  # hammer, but no intensity on this particular occurrence
            "instrument_id": instrument_id, "timeframe": TIMEFRAME, "ts": _dt(0),
            "activity_type": "candle_pattern", "activity": "hammer", "intensity": None,
            "open_price": 100, "high_price": 101, "low_price": 99, "close_price": 101.0,
        },
    ]
    queued = re_mod.on_activities(session_factory, SYMBOL, "NSE_EQ", activities)
    assert queued == []
