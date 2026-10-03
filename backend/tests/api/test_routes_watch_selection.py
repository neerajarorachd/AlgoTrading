def _register(client, symbol="RELIANCE"):
    return client.post("/api/symbols", json={"symbol": symbol, "exchange": "NSE", "segment": "EQUITY"}).get_json()["id"]


def test_get_on_a_fresh_instrument_is_empty(client):
    instrument_id = _register(client)
    resp = client.get(f"/api/instruments/{instrument_id}/watch-selection")
    assert resp.status_code == 200
    assert resp.get_json() == {"excluded": []}


def test_get_on_an_unknown_instrument_is_404(client):
    resp = client.get("/api/instruments/999999/watch-selection")
    assert resp.status_code == 404


def test_put_then_get_round_trips(client):
    instrument_id = _register(client)
    put_resp = client.put(f"/api/instruments/{instrument_id}/watch-selection", json={
        "excluded": [{"item_type": "pattern", "item_code": "doji"}, {"item_type": "strategy", "item_code": "11"}],
    })
    assert put_resp.status_code == 200
    assert sorted((e["item_type"], e["item_code"]) for e in put_resp.get_json()["excluded"]) == [
        ("pattern", "doji"), ("strategy", "11"),
    ]

    get_resp = client.get(f"/api/instruments/{instrument_id}/watch-selection")
    assert sorted((e["item_type"], e["item_code"]) for e in get_resp.get_json()["excluded"]) == [
        ("pattern", "doji"), ("strategy", "11"),
    ]


def test_put_on_an_unknown_instrument_is_404(client):
    resp = client.put("/api/instruments/999999/watch-selection", json={"excluded": []})
    assert resp.status_code == 404


def test_put_rejects_an_invalid_item_type(client):
    instrument_id = _register(client)
    resp = client.put(f"/api/instruments/{instrument_id}/watch-selection", json={
        "excluded": [{"item_type": "not_a_real_type", "item_code": "doji"}],
    })
    assert resp.status_code == 400


def test_put_rejects_a_missing_item_code(client):
    instrument_id = _register(client)
    resp = client.put(f"/api/instruments/{instrument_id}/watch-selection", json={
        "excluded": [{"item_type": "pattern"}],
    })
    assert resp.status_code == 400


def test_put_rejects_a_non_list_body(client):
    instrument_id = _register(client)
    resp = client.put(f"/api/instruments/{instrument_id}/watch-selection", json={"excluded": "doji"})
    assert resp.status_code == 400


def test_put_empty_list_clears_a_previous_selection(client):
    instrument_id = _register(client)
    client.put(f"/api/instruments/{instrument_id}/watch-selection", json={
        "excluded": [{"item_type": "pattern", "item_code": "doji"}],
    })
    resp = client.put(f"/api/instruments/{instrument_id}/watch-selection", json={"excluded": []})
    assert resp.status_code == 200
    assert resp.get_json() == {"excluded": []}
