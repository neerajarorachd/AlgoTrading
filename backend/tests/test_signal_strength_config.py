"""ALGO-6: everything about signal strength is configurable and calibratable
-- minimums (1-min base, user overrides), timeframe multipliers, target
share, recalibration (preview, then apply)."""
import random
from datetime import datetime, timedelta, timezone

import pytest

from db.models import CandleHistorical, InstrumentActivity, SubscribedSymbol
from db.ops import LibSignalStrength as ops
from signal_strength import scale_minimums
from signal_strength_calibration import apply, compute
from signal_strength_defaults import SIGNAL_STRENGTH_BASE

BASE_HAMMER = SIGNAL_STRENGTH_BASE["hammer"]["range_pct"]


def test_scaling_applies_to_price_measures_only():
    rules = {"hammer": {"range_pct": 0.05, "wick_ratio": 2.5}, "stoch_bullish_cross": {"gap": 5.0}}
    m = {"3min": 1.8, "5min": 2.4}
    assert scale_minimums(rules, "1min", m) == rules
    five = scale_minimums(rules, "5min", m)
    assert five["hammer"]["range_pct"] == pytest.approx(0.12) and five["hammer"]["wick_ratio"] == 2.5
    assert five["stoch_bullish_cross"]["gap"] == 5.0


def test_rules_for_timeframe_uses_the_multiplier_settings(session_factory):
    from db.ops import LibSystemSettings
    ops.set_rule(session_factory, "hammer", "range_pct", 0.05)
    LibSystemSettings.set_value(session_factory, "strength_multiplier_5min", 2.0)
    with session_factory() as s:
        assert ops.rules_for_timeframe(s, "5min")["hammer"]["range_pct"] == pytest.approx(0.10)
        assert ops.rules_for_timeframe(s, "1min")["hammer"]["range_pct"] == pytest.approx(0.05)


# ---- API ----

def test_api_lists_every_signal_with_per_timeframe_minimums(client):
    body = client.get("/api/signal-strength").get_json()
    hammer = next(s for s in body["signals"] if s["signal"] == "hammer")
    rng = next(m for m in hammer["measures"] if m["measure"] == "range_pct")
    assert rng["min"] == pytest.approx(BASE_HAMMER) and rng["scales_with_timeframe"]
    assert rng["by_timeframe"]["5min"] == pytest.approx(BASE_HAMMER * 2.36, rel=1e-3)
    wick = next(m for m in hammer["measures"] if m["measure"] == "wick_ratio")
    assert wick["by_timeframe"]["5min"] == wick["min"]  # ratios don't scale
    assert body["settings"]["strength_target_share"] == pytest.approx(0.40)


def test_api_user_override_and_system_edit(client):
    assert client.put("/api/signal-strength/rules", json={"signal": "hammer", "measure": "range_pct", "min": 0.08}).status_code == 200
    hammer = next(s for s in client.get("/api/signal-strength").get_json()["signals"] if s["signal"] == "hammer")
    rng = next(m for m in hammer["measures"] if m["measure"] == "range_pct")
    assert rng["min"] == 0.08 and rng["overridden"] and rng["system_min"] == pytest.approx(BASE_HAMMER)
    assert client.put("/api/signal-strength/rules", json={"signal": "hammer", "measure": "range_pct", "min": 0.06, "scope": "system"}).status_code == 200
    resp = client.put("/api/signal-strength/rules", json={"signal": "doji", "measure": "wick_ratio", "min": 2})
    assert resp.status_code == 400 and "has no strength measure" in resp.get_json()["error"]


def test_api_settings_are_editable_and_validated(client):
    assert client.put("/api/signal-strength/settings", json={"strength_multiplier_5min": 2.5, "strength_target_share": 0.3}).status_code == 200
    s = client.get("/api/signal-strength").get_json()["settings"]
    assert s["strength_multiplier_5min"] == 2.5 and s["strength_target_share"] == 0.3
    assert client.put("/api/signal-strength/settings", json={"strength_target_share": 1.5}).status_code == 400
    assert client.put("/api/signal-strength/settings", json={"colour": 1}).status_code == 400


# ---- calibration from history ----

def _seed_history(session_factory, days=3):
    """One stock, `days` sessions of 1-min candles, a doji every 4th candle."""
    rng = random.Random(3)
    with session_factory() as s:
        sym = SubscribedSymbol(symbol="TEST", exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
                               security_id="1", previous_close=100.0)
        s.add(sym)
        s.commit()
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        price = 100.0
        for d in range(days, 0, -1):
            open_utc = today - timedelta(days=d) + timedelta(hours=3, minutes=45)
            for i in range(120):
                o = price
                price = max(1.0, price + rng.gauss(0, 0.08))
                hi, lo = max(o, price) + rng.random() * 0.1, min(o, price) - rng.random() * 0.1
                ts = open_utc + timedelta(minutes=i)
                s.add(CandleHistorical(symbol="TEST", exchange_segment="NSE_EQ", timeframe="1min", ts=ts,
                                       open_price=o, high_price=hi, low_price=lo, close_price=price,
                                       volume=rng.randint(100, 5000)))
                if i % 4 == 3:
                    s.add(InstrumentActivity(instrument_id=sym.id, timeframe="1min", ts=ts, activity_type="candle_pattern",
                                             activity="doji", intensity=1.0, open_price=o, high_price=hi, low_price=lo,
                                             close_price=price))
        s.commit()


def test_calibration_targets_the_share_overall_and_measures_multipliers(session_factory):
    _seed_history(session_factory)
    p = compute(session_factory, target=0.40, sessions=3)
    assert p["stocks"] == 1 and set(p["base"]) == {"doji"}
    assert p["signals"]["doji"]["formations"] == 90
    assert 0.33 <= p["signals"]["doji"]["qualify_share"] <= 0.47
    assert 1 < p["multipliers"]["3min"] < p["multipliers"]["5min"]
    stricter = compute(session_factory, target=0.20, sessions=3)
    assert stricter["base"]["doji"]["range_pct"] > p["base"]["doji"]["range_pct"]


def test_calibration_preview_writes_nothing_until_applied(session_factory):
    _seed_history(session_factory)
    p = compute(session_factory, target=0.40, sessions=3)
    with session_factory() as s:
        assert ops.load_rules(s) == {}
    ops.set_rule(session_factory, "doji", "range_pct", 9.9, owner_id=1)  # a user override survives apply
    apply(session_factory, p)
    with session_factory() as s:
        assert ops.load_rules(s)["doji"]["range_pct"] == p["base"]["doji"]["range_pct"]
        assert ops.load_rules(s, owner_id=1)["doji"]["range_pct"] == 9.9
        assert ops.load_multipliers(s)["5min"] == p["multipliers"]["5min"]
