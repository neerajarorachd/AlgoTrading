def _register_symbol(client, symbol):
    return client.post("/api/symbols", json={"symbol": symbol, "exchange": "NSE", "segment": "EQUITY"}).get_json()["id"]


def test_create_watchlist_with_initial_instruments(client):
    reliance_id = _register_symbol(client, "RELIANCE")

    resp = client.post("/api/watchlists", json={"name": "My Picks", "instrument_ids": [reliance_id]})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["name"] == "My Picks"
    assert body["member_count"] == 1


def test_create_watchlist_requires_name(client):
    resp = client.post("/api/watchlists", json={})
    assert resp.status_code == 400


def test_get_watchlist_returns_members(client):
    reliance_id = _register_symbol(client, "RELIANCE")
    created = client.post("/api/watchlists", json={"name": "WL", "instrument_ids": [reliance_id]}).get_json()

    resp = client.get(f"/api/watchlists/{created['id']}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert [m["symbol"] for m in body["members"]] == ["RELIANCE"]


def test_get_unknown_watchlist_is_404(client):
    assert client.get("/api/watchlists/9999").status_code == 404


def test_list_watchlists_includes_member_counts(client):
    reliance_id = _register_symbol(client, "RELIANCE")
    tcs_id = _register_symbol(client, "TCS")
    client.post("/api/watchlists", json={"name": "WL1", "instrument_ids": [reliance_id, tcs_id]})
    client.post("/api/watchlists", json={"name": "WL2"})

    resp = client.get("/api/watchlists")
    assert resp.status_code == 200
    rows = {row["name"]: row["member_count"] for row in resp.get_json()}
    assert rows == {"WL1": 2, "WL2": 0}


def test_add_and_remove_instrument(client):
    reliance_id = _register_symbol(client, "RELIANCE")
    tcs_id = _register_symbol(client, "TCS")
    created = client.post("/api/watchlists", json={"name": "WL"}).get_json()

    add_resp = client.post(f"/api/watchlists/{created['id']}/instruments", json={"instrument_id": reliance_id})
    assert add_resp.status_code == 201
    client.post(f"/api/watchlists/{created['id']}/instruments", json={"instrument_id": tcs_id})

    fetched = client.get(f"/api/watchlists/{created['id']}").get_json()
    assert {m["symbol"] for m in fetched["members"]} == {"RELIANCE", "TCS"}

    remove_resp = client.delete(f"/api/watchlists/{created['id']}/instruments/{reliance_id}")
    assert remove_resp.status_code == 204

    fetched = client.get(f"/api/watchlists/{created['id']}").get_json()
    assert {m["symbol"] for m in fetched["members"]} == {"TCS"}


def test_add_unknown_instrument_id_is_404(client):
    created = client.post("/api/watchlists", json={"name": "WL"}).get_json()
    resp = client.post(f"/api/watchlists/{created['id']}/instruments", json={"instrument_id": 9999})
    assert resp.status_code == 404


def test_update_watchlist_name_and_description(client):
    created = client.post("/api/watchlists", json={"name": "Original"}).get_json()

    resp = client.put(f"/api/watchlists/{created['id']}", json={"name": "Renamed", "description": "new"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["name"] == "Renamed"
    assert body["description"] == "new"


def test_delete_watchlist_removes_it(client):
    created = client.post("/api/watchlists", json={"name": "ToDelete"}).get_json()

    resp = client.delete(f"/api/watchlists/{created['id']}")
    assert resp.status_code == 204
    assert client.get(f"/api/watchlists/{created['id']}").status_code == 404


def test_delete_unknown_watchlist_is_404(client):
    assert client.delete("/api/watchlists/9999").status_code == 404


def test_subscribe_watchlist_reactivates_a_stale_member(client):
    """The user's own framing: registering a watchlist for Market Watch —
    a member unregistered via DELETE /api/symbols/<id> (which doesn't touch
    WatchlistInstrument rows, see routes_watchlists.subscribe_watchlist's own
    docstring) must be reactivated and re-subscribed, not silently skipped."""
    reliance_id = _register_symbol(client, "RELIANCE")
    created = client.post("/api/watchlists", json={"name": "WL", "instrument_ids": [reliance_id]}).get_json()

    # unregister it after it's already a watchlist member
    assert client.delete(f"/api/symbols/{reliance_id}").status_code == 204
    assert client.get("/api/symbols").get_json() == []

    resp = client.post(f"/api/watchlists/{created['id']}/subscribe")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["subscribed"] == ["RELIANCE"]
    assert body["already_active"] == []
    assert body["failed"] == []

    # MarketWatch.jsx reads from exactly this endpoint -- confirm the member
    # is really visible there again, not just flagged in the response.
    symbols = client.get("/api/symbols").get_json()
    assert [s["symbol"] for s in symbols] == ["RELIANCE"]


def test_subscribe_watchlist_skips_already_active_members(client):
    reliance_id = _register_symbol(client, "RELIANCE")
    created = client.post("/api/watchlists", json={"name": "WL", "instrument_ids": [reliance_id]}).get_json()

    resp = client.post(f"/api/watchlists/{created['id']}/subscribe")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["subscribed"] == []
    assert body["already_active"] == ["RELIANCE"]


def test_subscribe_unknown_watchlist_is_404(client):
    assert client.post("/api/watchlists/9999/subscribe").status_code == 404
