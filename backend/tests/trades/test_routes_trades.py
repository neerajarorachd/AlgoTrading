from datetime import datetime

from db.models import SubscribedSymbol

NOW = datetime(2026, 11, 11, 4, 30)  # 10:00 IST


def _setup(app):
    eng = app.extensions["trade_engine"]
    eng.now = lambda: NOW
    eng.price_lookup = lambda s, e: 250.0
    sf = app.extensions["db_session_factory"]
    with sf() as s:
        row = SubscribedSymbol(symbol="ITC", exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
                               security_id="1660", active=True)
        s.add(row)
        s.commit()
        return row.id


def test_place_list_modify_exit_summary(app):
    iid = _setup(app)
    c = app.test_client()
    r = c.post("/api/trades", json={"instrument_id": iid, "direction": "buy", "quantity": 4,
                                    "stop_price": 245, "target_price": 260, "screen_signal": "Buy"})
    assert r.status_code == 201, r.json
    tid = r.json["id"]
    assert r.json["status"] == "open" and r.json["expiry_date"] == "2026-11-23"
    assert r.json["running"]["ltp"] == 250.0 and r.json["est_costs"] > 0
    assert r.json["running"]["net_pnl"] == -r.json["est_costs"]  # gross 0 - rough expenses
    d = c.get(f"/api/trades/{tid}").json
    assert d["exact"]["gross_pnl"] == 0 and d["exact"]["costs"] > 0
    active = c.get("/api/trades").json
    assert [t["id"] for t in active] == [tid]
    assert c.patch(f"/api/trades/{tid}", json={"stop_price": 248}).json["stop_price"] == 248
    assert c.patch(f"/api/trades/{tid}", json={"stop_price": 251}).status_code == 400
    assert c.get("/api/trades/account").json["used"] == 2000.0  # stop + target legs: 2x
    out = c.post(f"/api/trades/{tid}/exit").json
    assert out["status"] == "closed" and out["exit_reason"] == "manual"
    assert len(c.get(f"/api/trades/{tid}/snapshots").json) == 0  # test engine has no candles yet
    assert c.get("/api/trades?scope=all&days=3650").json[0]["status"] == "closed"
    summ = c.get("/api/trades/summary?days=3650").json
    assert summ["all"]["trades"] == 1 and summ["by_expiry"]["other"]["trades"] == 1


def test_live_mode_refused_and_bad_input(app):
    iid = _setup(app)
    c = app.test_client()
    r = c.post("/api/trades", json={"instrument_id": iid, "direction": "buy", "quantity": 1, "mode": "live"})
    assert r.status_code == 400 and "coming soon" in r.json["error"]
    assert c.post("/api/trades", json={"direction": "buy", "quantity": 1}).status_code == 400
    assert c.post("/api/trades", json={"instrument_id": iid, "direction": "up", "quantity": 1}).status_code == 400
    assert c.post("/api/trades", json={"instrument_id": iid, "direction": "buy", "quantity": 0}).status_code == 400


def test_orders_api_pending_and_cancel(app):
    iid = _setup(app)
    c = app.test_client()
    tid = c.post("/api/trades", json={"instrument_id": iid, "direction": "buy", "quantity": 2,
                                      "stop_price": 245, "target_price": 260}).json["id"]
    pend = c.get("/api/trades/orders?status=pending").json
    assert sorted(o["kind"] for o in pend) == ["stop", "target"] and pend[0]["trade_direction"] == "buy"
    stop = next(o for o in pend if o["kind"] == "stop")
    assert c.delete(f"/api/trades/orders/{stop['id']}").json["stop_price"] is None
    assert [o["kind"] for o in c.get("/api/trades/orders?status=pending").json] == ["target"]
    assert c.delete(f"/api/trades/orders/{stop['id']}").status_code == 400
    assert len(c.get(f"/api/trades/orders?trade_id={tid}").json) == 3
