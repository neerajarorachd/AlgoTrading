from db.models import RecommendationSystem
from db.ops import LibRecommendationSystems


def test_seed_defaults_is_idempotent_and_never_overwrites_a_tuned_row(session_factory):
    seed = [{
        "code": "RS1", "name": "Original name", "kind": "system",
        "watchlist_id": None, "timeframe": "3min", "top_n_per_run": 3,
    }]
    LibRecommendationSystems.seed_defaults(session_factory, seed)

    with session_factory() as session:
        rs = LibRecommendationSystems.get_by_code(session, "RS1")
        assert rs.name == "Original name"
        assert rs.timeframe == "3min"
        rs.timeframe = "5min"  # simulate an operator tuning it
        rs.top_n_per_run = 1
        session.commit()

    # Re-seeding (e.g. next app startup) must not clobber the tune.
    LibRecommendationSystems.seed_defaults(session_factory, seed)
    with session_factory() as session:
        rs = LibRecommendationSystems.get_by_code(session, "RS1")
        assert rs.timeframe == "5min"
        assert rs.top_n_per_run == 1


def test_get_by_id_and_list_all_active_only(session_factory):
    with session_factory() as session:
        session.add_all([
            RecommendationSystem(code="RS1", name="One", is_active=True, timeframe="3min"),
            RecommendationSystem(code="RS2", name="Two", is_active=False, timeframe="5min"),
        ])
        session.commit()

    with session_factory() as session:
        rs1 = LibRecommendationSystems.get_by_code(session, "RS1")
        by_id = LibRecommendationSystems.get_by_id(session, rs1.id)
        assert by_id.code == "RS1"

        all_rows = LibRecommendationSystems.list_all(session)
        active_rows = LibRecommendationSystems.list_all(session, active_only=True)
    assert {r.code for r in all_rows} == {"RS1", "RS2"}
    assert {r.code for r in active_rows} == {"RS1"}


def test_resolve_settings_with_no_rs_returns_global_unchanged():
    global_settings = {"recommendation_win_pct_threshold": 0.70, "recommendation_min_sample_size": 10}
    resolved = LibRecommendationSystems.resolve_settings(None, global_settings)
    assert resolved == global_settings
    assert resolved is not global_settings  # a defensive copy, not the same dict


def test_resolve_settings_overrides_only_the_columns_that_are_set(session_factory):
    with session_factory() as session:
        rs = RecommendationSystem(
            code="RS1", name="RS1", timeframe="3min",
            win_pct_threshold_override=0.80,  # tuned
            min_sample_size_override=None,     # inherit global
        )
        session.add(rs)
        session.commit()
        session.refresh(rs)

        global_settings = {
            "recommendation_win_pct_threshold": 0.70,
            "recommendation_min_sample_size": 10,
            "recommendation_wilson_confidence": 0.95,
        }
        resolved = LibRecommendationSystems.resolve_settings(rs, global_settings)

    assert resolved["recommendation_win_pct_threshold"] == 0.80  # overridden
    assert resolved["recommendation_min_sample_size"] == 10       # inherited
    assert resolved["recommendation_wilson_confidence"] == 0.95   # untouched key
