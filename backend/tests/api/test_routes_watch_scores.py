def _register(client, symbol="RELIANCE"):
    return client.post("/api/symbols", json={"symbol": symbol, "exchange": "NSE", "segment": "EQUITY"}).get_json()["id"]


def test_returns_one_row_per_active_instrument(client):
    a = _register(client, "RELIANCE")
    b = _register(client, "TCS")
    resp = client.get("/api/watch-scores")
    assert resp.status_code == 200
    body = resp.get_json()
    ids = {r["instrument_id"] for r in body["scores"]}
    assert {a, b} <= ids
    for r in body["scores"]:
        assert r["bucket"] in body["bucket_colors"]


def test_a_fresh_instrument_with_no_activity_is_quiet(client):
    instrument_id = _register(client)
    resp = client.get("/api/watch-scores")
    row = next(r for r in resp.get_json()["scores"] if r["instrument_id"] == instrument_id)
    assert row == {"instrument_id": instrument_id, "bull_score": 0, "bear_score": 0, "bucket": "quiet"}


def test_rejects_an_invalid_timeframe(client):
    resp = client.get("/api/watch-scores?timeframe=7min")
    assert resp.status_code == 400


def test_rejects_a_non_integer_window(client):
    resp = client.get("/api/watch-scores?window=abc")
    assert resp.status_code == 400


def test_rejects_a_non_positive_window(client):
    resp = client.get("/api/watch-scores?window=0")
    assert resp.status_code == 400


def test_accepts_a_custom_timeframe_and_window(client):
    _register(client)
    resp = client.get("/api/watch-scores?timeframe=1min&window=5")
    assert resp.status_code == 200
