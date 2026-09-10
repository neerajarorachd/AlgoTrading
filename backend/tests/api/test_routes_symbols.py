def test_search_instruments_endpoint(client):
    resp = client.get("/api/instruments/search?q=nalco&exchange=NSE&segment=EQUITY")
    assert resp.status_code == 200
    results = resp.get_json()
    assert len(results) == 1
    assert results[0]["symbol"] == "NALCO"  # FakeInstrumentMaster.search() echoes the query


def test_search_instruments_empty_query_returns_empty_list(client):
    resp = client.get("/api/instruments/search")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_post_symbol_creates_row_and_subscribes(client, fake_broker):
    resp = client.post("/api/symbols", json={"symbol": "RELIANCE", "exchange": "NSE", "segment": "EQUITY"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["symbol"] == "RELIANCE"
    assert body["exchange_segment"] == "NSE_EQ"
    assert body["security_id"] == "SEC-RELIANCE"
    assert body["active"] is True

    assert len(fake_broker.subscribed_calls) == 1
    assert fake_broker.subscribed_calls[0][0]["security_id"] == "SEC-RELIANCE"


def test_post_symbol_is_idempotent_when_already_active(client, fake_broker):
    client.post("/api/symbols", json={"symbol": "TCS", "exchange": "NSE", "segment": "EQUITY"})
    resp = client.post("/api/symbols", json={"symbol": "TCS", "exchange": "NSE", "segment": "EQUITY"})

    assert resp.status_code == 200
    assert len(fake_broker.subscribed_calls) == 1  # no duplicate subscribe

    listed = client.get("/api/symbols").get_json()
    assert len([row for row in listed if row["symbol"] == "TCS"]) == 1


def test_post_symbol_without_symbol_field_is_400(client):
    resp = client.post("/api/symbols", json={"exchange": "NSE"})
    assert resp.status_code == 400


def test_delete_then_post_reactivates_same_row(client, fake_broker):
    created = client.post("/api/symbols", json={"symbol": "INFY", "exchange": "NSE", "segment": "EQUITY"}).get_json()
    row_id = created["id"]

    del_resp = client.delete(f"/api/symbols/{row_id}")
    assert del_resp.status_code == 204
    assert len(fake_broker.unsubscribed_calls) == 1

    active_after_delete = client.get("/api/symbols").get_json()
    assert all(row["id"] != row_id for row in active_after_delete)

    reactivated = client.post("/api/symbols", json={"symbol": "INFY", "exchange": "NSE", "segment": "EQUITY"}).get_json()
    assert reactivated["id"] == row_id  # same row, not a new one — unique constraint upheld
    assert reactivated["active"] is True
    assert len(fake_broker.subscribed_calls) == 2  # original register + reactivate


def test_delete_unknown_id_is_404(client):
    resp = client.delete("/api/symbols/999999")
    assert resp.status_code == 404


def test_delete_already_removed_id_is_idempotent_204(client, fake_broker):
    created = client.post("/api/symbols", json={"symbol": "WIPRO", "exchange": "NSE", "segment": "EQUITY"}).get_json()
    row_id = created["id"]

    first = client.delete(f"/api/symbols/{row_id}")
    second = client.delete(f"/api/symbols/{row_id}")

    assert first.status_code == 204
    assert second.status_code == 204
    assert len(fake_broker.unsubscribed_calls) == 1  # second delete didn't call unsubscribe again


def test_post_symbol_unknown_instrument_is_404_not_500(client):
    resp = client.post("/api/symbols", json={"symbol": "UNKNOWN", "exchange": "NSE", "segment": "EQUITY"})
    assert resp.status_code == 404
    assert "Unknown instrument" in resp.get_json()["error"]


def test_post_symbol_broker_failure_is_502_not_500(client):
    resp = client.post("/api/symbols", json={"symbol": "BROKER-DOWN", "exchange": "NSE", "segment": "EQUITY"})
    assert resp.status_code == 502
    assert "Broker quote lookup failed" in resp.get_json()["error"]


def test_list_symbols_only_returns_active_rows(client):
    created = client.post("/api/symbols", json={"symbol": "HDFC", "exchange": "NSE", "segment": "EQUITY"}).get_json()
    client.post("/api/symbols", json={"symbol": "ITC", "exchange": "NSE", "segment": "EQUITY"})
    client.delete(f"/api/symbols/{created['id']}")

    listed = client.get("/api/symbols").get_json()
    symbols = {row["symbol"] for row in listed}
    assert symbols == {"ITC"}
