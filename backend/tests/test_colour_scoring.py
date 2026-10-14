"""ALGO-33: stock colour from patterns -- tiers from the top weight x match %,
the 80% formed cut-off, pink/red overrides, direction from the top pattern,
sort values (green highest, red 0)."""
from dataclasses import dataclass

import pytest

from colour_scoring import COLOUR_VALUE, colour_for


@dataclass
class P:
    key: str
    pattern_type: str
    weight: float
    id: int = 0


BULL5 = P("departure_bull", "bullish", 5)
BULL4 = P("momentum_bull", "bullish", 4)
BEAR4 = P("momentum_bear", "bearish", 4)
BULL3 = P("hammer", "bullish", 3)
BULL2 = P("confluence", "bullish", 2)
CAUTION = P("squeeze", "caution", 0)
AVOID = P("chop", "avoid", 0)
ALL = [BULL5, BULL4, BEAR4, BULL3, BULL2, CAUTION, AVOID]


@pytest.mark.parametrize("matches, colour", [
    ({"departure_bull": 1.0}, "dark_green"),          # 5 x 100% = 5
    ({"departure_bull": 0.8}, "light_green"),         # 5 x 80% = 4
    ({"momentum_bull": 1.0}, "light_green"),          # 4 x 100% = 4
    ({"hammer": 1.0}, "orange"),                      # 3
    ({"momentum_bull": 0.8}, "orange"),               # 4 x 80% = 3.2
    ({"confluence": 1.0}, "yellow"),                  # 2
    ({"departure_bull": 0.6}, "white"),               # 60% < 80%: not formed (5 x 0.6 = 0)
    ({}, "white"),
])
def test_tiers(matches, colour):
    assert colour_for(matches, ALL, 0.8)["colour"] == colour


def test_top_one_decides_among_several():
    r = colour_for({"confluence": 1.0, "hammer": 1.0, "departure_bull": 0.8}, ALL, 0.8)
    assert r["colour"] == "light_green" and r["top_pattern"] == "departure_bull" and r["top_score"] == pytest.approx(4.0)


def test_pink_and_red_override_every_positive_colour_and_red_beats_pink():
    assert colour_for({"departure_bull": 1.0, "squeeze": 1.0}, ALL, 0.8)["colour"] == "pink"
    assert colour_for({"departure_bull": 1.0, "chop": 1.0}, ALL, 0.8)["colour"] == "red"
    assert colour_for({"squeeze": 1.0, "chop": 1.0}, ALL, 0.8)["colour"] == "red"
    assert colour_for({"squeeze": 0.5}, ALL, 0.8)["colour"] == "white"  # a warning must be formed too


def test_direction_comes_from_the_top_pattern():
    assert colour_for({"momentum_bear": 1.0, "hammer": 1.0}, ALL, 0.8)["direction"] == "bear"
    assert colour_for({"momentum_bear": 1.0, "departure_bull": 1.0}, ALL, 0.8)["direction"] == "bull"
    assert colour_for({}, ALL, 0.8)["direction"] is None


def test_per_stock_weight_is_used_when_given():
    r = colour_for({"hammer": 1.0}, ALL, 0.8, weight_of=lambda p: 5 if p.key == "hammer" else p.weight)
    assert r["colour"] == "dark_green"


def test_sort_values_green_top_red_bottom():
    order = ["dark_green", "light_green", "orange", "yellow", "white", "pink", "red"]
    assert [COLOUR_VALUE[c] for c in order] == [6, 5, 4, 3, 2, 1, 0]


# ---- API ----

def _register(client, symbol):
    return client.post("/api/symbols", json={"symbol": symbol, "exchange": "NSE", "segment": "EQUITY"}).get_json()["id"]


def test_api_defaults_to_pattern_colours(app, client):
    iid = _register(client, "RELIANCE")
    ev = app.extensions["activity_engine"].pattern_evaluator
    departure = next(p for p in ev.patterns if p.key == "macd_departure_bull")
    ev.scores[("RELIANCE", "NSE_EQ", "3min")] = {departure.key: 1.0}
    body = client.get("/api/watch-scores?timeframe=3min").get_json()
    row = next(r for r in body["scores"] if r["instrument_id"] == iid)
    assert body["mode"] == "patterns" and row["bucket"] == row["colour"] == "dark_green"
    assert row["direction"] == "bull" and row["colour_value"] == 6
    assert body["bucket_colors"]["red"] and body["bucket_values"]["red"] == 0


def test_api_stock_with_no_scores_is_white(client):
    iid = _register(client, "TCS")
    row = next(r for r in client.get("/api/watch-scores").get_json()["scores"] if r["instrument_id"] == iid)
    assert row["colour"] == "white" and row["direction"] is None


def test_api_all_timeframes_strongest_wins_but_warnings_override(app, client):
    iid = _register(client, "INFY")
    ev = app.extensions["activity_engine"].pattern_evaluator
    ev.scores[("INFY", "NSE_EQ", "1min")] = {"macd_stoch_bull_confluence": 1.0}   # yellow
    ev.scores[("INFY", "NSE_EQ", "5min")] = {"macd_departure_bear": 1.0}          # dark green, bear
    row = next(r for r in client.get("/api/watch-scores?timeframe=all").get_json()["scores"] if r["instrument_id"] == iid)
    assert row["colour"] == "dark_green" and row["direction"] == "bear"
    assert {tf: r["colour"] for tf, r in row["by_timeframe"].items()} == {"1min": "yellow", "3min": "white", "5min": "dark_green"}
    ev.scores[("INFY", "NSE_EQ", "3min")] = {"too_volatile": 1.0}                 # red on one timeframe
    row = next(r for r in client.get("/api/watch-scores?timeframe=all").get_json()["scores"] if r["instrument_id"] == iid)
    assert row["colour"] == "red" and row["warnings"] == ["too_volatile"]


def test_api_rejects_unknown_timeframe(client):
    assert client.get("/api/watch-scores?timeframe=15min").status_code == 400
