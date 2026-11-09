def test_selection_upsert_with_manual_settings(app):
    c = app.test_client()
    r = c.put("/api/paper/selections", json={"item_key": "macd_widening_trend_bull", "stop_mode": "pct",
                                              "stop_value": 0.5, "size_mode": "qty", "quantity_per_trade": 50})
    assert r.status_code == 200 and r.json["quantity_per_trade"] == 50 and r.json["target_mode"] == "atr"
    r = c.put("/api/paper/selections", json={"item_key": "macd_widening_trend_bull", "enabled": False})
    assert r.json["enabled"] is False and r.json["stop_value"] == 0.5  # same row updated
    assert len(c.get("/api/paper/selections").json) == 1
    assert c.put("/api/paper/selections", json={"item_key": "x", "stop_mode": "bad"}).status_code == 400
    assert c.delete(f"/api/paper/selections/{r.json['id']}").status_code == 200
    assert c.get("/api/paper/live/trades").json == []


def test_backtest_master_with_three_runs(app):
    c = app.test_client()
    body = {"name": "MACD variants", "symbols": [1, 2], "date_from": "2025-11-01", "date_to": "2026-10-31",
            "runs": [{"label": "A", "items": ["p1"]},
                     {"label": "B", "items": ["p2"], "stop_mode": "points", "stop_value": 3},
                     {"label": "C", "items": ["p3"], "size_mode": "qty", "quantity_per_trade": 10}]}
    r = c.post("/api/paper/backtests", json=body)
    assert r.status_code == 201 and r.json["status"] == "queued"
    assert [x["label"] for x in r.json["runs"]] == ["A", "B", "C"]
    assert r.json["runs"][1]["stop_mode"] == "points" and r.json["runs"][2]["quantity_per_trade"] == 10
    assert c.get(f"/api/paper/backtests/{r.json['id']}").json["runs"][0]["items"][0]["item_key"] == "p1"
    assert len(c.get("/api/paper/backtests").json) == 1
    bad = dict(body, runs=[{"label": "A", "items": []}])
    assert c.post("/api/paper/backtests", json=bad).status_code == 400
