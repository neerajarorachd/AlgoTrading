def _register(client, symbol="RELIANCE"):
    return client.post("/api/symbols", json={"symbol": symbol, "exchange": "NSE", "segment": "EQUITY"}).get_json()["id"]


def test_unknown_instrument_is_404(client):
    resp = client.get("/api/instruments/999999/order-popup")
    assert resp.status_code == 404


def test_no_directional_signal_yet_is_404(client):
    instrument_id = _register(client)
    resp = client.get(f"/api/instruments/{instrument_id}/order-popup")
    assert resp.status_code == 404


def test_rejects_an_invalid_timeframe(client):
    instrument_id = _register(client)
    resp = client.get(f"/api/instruments/{instrument_id}/order-popup?timeframe=7min")
    assert resp.status_code == 400
