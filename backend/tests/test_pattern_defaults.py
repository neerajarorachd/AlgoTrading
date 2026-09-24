from datetime import datetime, timezone
from types import SimpleNamespace

import pattern_defaults as pd_mod
import recommendation_engine as re_mod
import recommendation_outcomes as ro
from db.models import (
    GuidingScenario, Recommendation, RecommendationSystem, RecommendationSystemPatternDefault, SubscribedSymbol,
)
from db.ops import LibRecommendationSystems, LibSystemSettings
from stats_utils import wilson_lower_bound


def _default(**kw):
    base = dict(size_mode="max_qty", max_qty=100, fund_pct=None, sl_pct=0.4, target_pct=0.8)
    base.update(kw)
    return SimpleNamespace(**base)


# ------------------------------------------------------------------ suggest_order

def test_bull_sl_below_target_above():
    s = pd_mod.suggest_order(_default(), "bull", 530.0, None)
    assert s["suggested_sl_price"] == 527.88 and s["suggested_target_price"] == 534.24
    assert s["suggested_quantity"] == 100


def test_bear_sl_above_target_below():
    s = pd_mod.suggest_order(_default(), "bear", 530.0, None)
    assert s["suggested_sl_price"] == 532.12 and s["suggested_target_price"] == 525.76


def test_available_fund_sizes_a_percent_of_capital_over_price():
    d = _default(size_mode="available_fund", max_qty=None, fund_pct=50.0)
    assert pd_mod.suggest_order(d, "bull", 500.0, 200_000)["suggested_quantity"] == 200  # 100k / 500
    # nothing to size against -> no quantity, but SL/target are still suggested
    s = pd_mod.suggest_order(d, "bull", 500.0, None)
    assert s["suggested_quantity"] is None and s["suggested_sl_price"] is not None


# ------------------------------------------------------------------ DB lookup / seeding

def _rs(session_factory):
    LibRecommendationSystems.seed_defaults(session_factory, re_mod.RECOMMENDATION_SYSTEM_SEED)
    with session_factory() as session:
        return LibRecommendationSystems.get_by_code(session, "RS1").id


def test_fallback_row_is_created_once_per_rs(session_factory):
    rs_id = _rs(session_factory)
    pd_mod.ensure_fallback_defaults(session_factory)
    pd_mod.ensure_fallback_defaults(session_factory)
    with session_factory() as session:
        rows = session.query(RecommendationSystemPatternDefault).filter_by(recommendation_system_id=rs_id).all()
    assert [r.pattern for r in rows] == ["*"] and rows[0].max_qty == 1


def test_a_patterns_own_row_beats_the_fallback(session_factory):
    rs_id = _rs(session_factory)
    pd_mod.ensure_fallback_defaults(session_factory)
    with session_factory() as session:
        session.add(RecommendationSystemPatternDefault(
            recommendation_system_id=rs_id, pattern="hammer", size_mode="max_qty", max_qty=7,
            sl_pct=0.2, target_pct=0.4))
        session.commit()
        assert pd_mod.find_default(session, rs_id, "hammer").max_qty == 7
        assert pd_mod.find_default(session, rs_id, "doji").pattern == "*"


# ------------------------------------------------------------------ API

def _rs1(client):
    return next(r for r in client.get("/api/recommendation-systems").get_json() if r["code"] == "RS1")


def _row(pattern="*", **kw):
    base = {"pattern": pattern, "size_mode": "max_qty", "max_qty": 10, "sl_pct": 0.4, "target_pct": 0.8}
    base.update(kw)
    return base


def test_defaults_api_round_trip_and_fallback_first(client):
    rs_id = _rs1(client)["id"]
    assert [d["pattern"] for d in client.get(f"/api/recommendation-systems/{rs_id}/pattern-defaults").get_json()] == ["*"]
    resp = client.put(f"/api/recommendation-systems/{rs_id}/pattern-defaults", json={"defaults": [
        _row("hammer", size_mode="available_fund", max_qty=None, fund_pct=25), _row("*")]})
    assert resp.status_code == 200
    got = resp.get_json()
    assert [d["pattern"] for d in got] == ["*", "hammer"]
    assert got[1]["fund_pct"] == 25.0 and got[1]["max_qty"] is None


def test_defaults_api_validation(client):
    rs_id = _rs1(client)["id"]
    url = f"/api/recommendation-systems/{rs_id}/pattern-defaults"
    bad = [
        [_row("hammer")],                                              # no fallback row
        [_row("*"), _row("*")],                                        # duplicate pattern
        [_row("*", size_mode="lots")],                                 # bad mode
        [_row("*", max_qty=0)],                                        # non-positive qty
        [_row("*", size_mode="available_fund", fund_pct=150)],         # >100 %
        [_row("*", sl_pct=0)],                                         # non-positive SL
    ]
    for defaults in bad:
        assert client.put(url, json={"defaults": defaults}).status_code == 400, defaults
    assert client.put(url, json={}).status_code == 400
    assert client.get("/api/recommendation-systems/9999/pattern-defaults").status_code == 404


def test_fallback_capital_is_a_setting(client):
    rs_id = _rs1(client)["id"]
    assert client.put(f"/api/recommendation-systems/{rs_id}", json={"fallback_capital": 250000}).status_code == 200
    assert _rs1(client)["fallback_capital"] == 250000.0


# ------------------------------------------------------------------ live recommendation carries the suggestion

def test_generated_recommendation_carries_suggested_qty_sl_and_target(session_factory):
    LibSystemSettings.seed_defaults(session_factory, re_mod.SYSTEM_SETTING_SEED)
    rs_id = _rs(session_factory)
    pd_mod.ensure_fallback_defaults(session_factory)
    with session_factory() as session:
        inst = SubscribedSymbol(symbol="RELIANCE", exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
                                security_id="S1", previous_close=100.0)
        session.add(inst)
        session.commit()
        session.add(GuidingScenario(
            instrument_id=inst.id, timeframe="3min", pattern="hammer", window_kind="2y", direction="bull",
            band_min=0.5, band_max=1.5, checkpoint=20, sample_count=10, win_count=8, win_pct=0.8,
            wilson_score=wilson_lower_bound(8, 10, 0.95), win_pct_threshold_applied=0.7,
            min_sample_size_applied=10))
        session.commit()
        inst_id = inst.id
    result = re_mod.generate_recommendations(
        session_factory, instrument_id=inst_id, timeframe="3min", pattern="hammer", direction="bull",
        entry_price=100.0, detected_ts=datetime.now(timezone.utc), intensity=1.0, recommendation_system_id=rs_id)
    with session_factory() as session:
        row = session.query(Recommendation).filter_by(id=result["id"]).one()
    assert row.suggested_quantity == 1 and float(row.suggested_sl_price) == 99.6
    assert float(row.suggested_target_price) == 100.8


# ------------------------------------------------------------------ first-hit outcome

def _rec(direction="bull", sl=99.0, tg=102.0):
    return SimpleNamespace(direction=direction, suggested_sl_price=sl, suggested_target_price=tg)


def _w(*highs_lows):  # (ts, close, high, low)
    return [(i, 100.0, h, l) for i, (h, l) in enumerate(highs_lows)]


def test_target_reached_first():
    assert ro.first_hit(_rec(), _w((100.5, 99.5), (101.0, 99.5), (102.2, 100.0))) == ("target", 3)


def test_stop_reached_first():
    assert ro.first_hit(_rec(), _w((100.5, 99.5), (100.5, 98.9))) == ("sl", 2)


def test_a_candle_touching_both_counts_as_the_stop():
    assert ro.first_hit(_rec(), _w((102.5, 98.5))) == ("sl", 1)


def test_neither_reached_is_none_and_bear_is_mirrored():
    assert ro.first_hit(_rec(), _w((100.5, 99.5))) == ("none", None)
    bear = _rec("bear", sl=101.0, tg=98.0)
    assert ro.first_hit(bear, _w((100.5, 99.5), (100.6, 97.9))) == ("target", 2)
    assert ro.first_hit(bear, _w((101.2, 99.5))) == ("sl", 1)


def test_no_suggested_levels_means_no_first_hit():
    assert ro.first_hit(_rec(sl=None, tg=None), _w((105.0, 95.0))) == (None, None)
