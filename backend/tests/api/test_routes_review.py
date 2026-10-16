"""Hit review API (2026-10-16): today's pattern hits / raw signals with the
numbers recorded live."""
from datetime import datetime, timezone


def _hit(pattern="macd_departure_bull", tf="3min", minute=0, met=3, of=3):
    return {"ts": datetime(2026, 10, 16, 4, minute, tzinfo=timezone.utc), "timeframe": tf, "pattern": pattern,
            "name": "MACD departure (bullish)", "type": "bullish", "weight": 5.0, "match": met / of, "score": 5.0 * met / of,
            "met": met, "of": of, "close": 100.0,
            "conditions": [{"formula": "BB_WIDTH > 1", "required": "> 1.0", "actual": 1.2, "met": True}]}


def test_pattern_review_one_stock_every_timeframe(app, client):
    ev = app.extensions["activity_engine"].pattern_evaluator
    ev.explanations[("RELIANCE", "NSE_EQ", "1min")] = [_hit(tf="1min", minute=1)]
    ev.explanations[("RELIANCE", "NSE_EQ", "3min")] = [_hit(minute=3), _hit(pattern="bullish_momentum", minute=6)]
    ev.explanations[("TCS", "NSE_EQ", "3min")] = [_hit(minute=9)]
    body = client.get("/api/review?kind=pattern&key=macd_departure_bull&symbol=RELIANCE").get_json()
    assert body["name"] == "MACD departure (bullish)" and len(body["conditions"]) == 3
    assert [(h["symbol"], h["timeframe"], h["time"]) for h in body["hits"]] == [("RELIANCE", "1min", "09:31"), ("RELIANCE", "3min", "09:33")]
    assert body["hits"][0]["conditions"][0]["actual"] == 1.2
    every = client.get("/api/review?kind=pattern&key=macd_departure_bull&all=1").get_json()
    assert {h["symbol"] for h in every["hits"]} == {"RELIANCE", "TCS"}


def test_signal_review_lists_measures_with_minimums(app, client):
    engine = app.extensions["activity_engine"]
    engine.signal_explanations[("RELIANCE", "NSE_EQ", "1min")] = [{
        "ts": datetime(2026, 10, 16, 4, 5, tzinfo=timezone.utc), "timeframe": "1min", "signal": "hammer", "kind": "candle_pattern",
        "measures": [{"measure": "range_pct", "formula": "RANGE / CLOSE * 100", "required": ">= 0.05", "actual": 0.2, "met": True}],
        "strong": True}]
    body = client.get("/api/review?kind=signal&key=hammer&symbol=RELIANCE").get_json()
    assert body["kind"] == "signal" and {m["measure"] for m in body["measures"]} == {"range_pct", "wick_ratio", "volume_ratio"}
    assert body["hits"][0]["strong"] is True and body["hits"][0]["time"] == "09:35"


def test_review_needs_kind_key_and_symbol(client):
    assert client.get("/api/review?kind=x&key=a&symbol=B").status_code == 400
    assert client.get("/api/review?kind=pattern&key=a").status_code == 400
