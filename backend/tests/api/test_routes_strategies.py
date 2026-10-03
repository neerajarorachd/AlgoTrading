def _condition(element_code, **overrides):
    """Full-key condition dict — matches exactly what GET /api/strategies/:id
    always returns (every optional field present, None where unset), so
    equality comparisons against a response body are apples-to-apples."""
    base = {
        "element_code": element_code, "operator": None, "compare_type": None,
        "compared_element_code": None, "static_value": None,
        "static_value_min": None, "static_value_max": None, "static_value_step": None,
        "left_formula": None, "right_formula": None,
    }
    base.update(overrides)
    return base


NESTED_TREE = {
    "operator": "OR",
    "conditions": [
        _condition("ma21", operator=">", compare_type="element", compared_element_code="vwap"),
    ],
    "groups": [
        {
            "operator": "AND",
            "conditions": [
                _condition("rsi", operator="<", compare_type="static", static_value=40.0),
                _condition("doji"),
            ],
            "groups": [],
        },
    ],
}


def test_list_strategy_elements_includes_seeded_event_and_numeric_entries(client):
    resp = client.get("/api/strategy-elements")
    assert resp.status_code == 200
    elements = {e["code"]: e for e in resp.get_json()}
    assert elements["doji"]["element_type"] == "event"
    assert elements["rsi"]["element_type"] == "numeric"
    assert elements["rsi"]["source"] == "candle_indicators"
    # kind: PatternDefinition's finer category, joined in for the Market
    # Watch "what to watch" picker (2026-10-04) -- present for a pattern
    # element, None for a numeric (not-a-pattern) one.
    assert elements["doji"]["kind"] == "single_candle"
    assert elements["macd_bullish_cross"]["kind"] == "indicator"
    assert elements["rsi"]["kind"] is None


def test_create_strategy_requires_name_and_type(client):
    resp = client.post("/api/strategies", json={"name": "Missing type"})
    assert resp.status_code == 400


def test_create_strategy_rejects_an_unknown_element_code(client):
    resp = client.post("/api/strategies", json={
        "name": "Bad element", "strategy_type": "entry",
        "tree": {"operator": "AND", "conditions": [{"element_code": "not_a_real_element"}], "groups": []},
    })
    assert resp.status_code == 400
    assert "not_a_real_element" in resp.get_json()["error"]


def test_create_and_get_strategy_round_trips_the_tree(client):
    resp = client.post("/api/strategies", json={
        "name": "My Strategy", "strategy_type": "entry", "description": "test", "tree": NESTED_TREE,
    })
    assert resp.status_code == 201
    created = resp.get_json()
    assert created["name"] == "My Strategy"
    assert created["tree"] == NESTED_TREE

    fetched = client.get(f"/api/strategies/{created['id']}").get_json()
    assert fetched["tree"] == NESTED_TREE
    assert fetched["description"] == "test"


def test_list_strategies_returns_metadata_without_tree(client):
    client.post("/api/strategies", json={"name": "Alpha", "strategy_type": "entry", "tree": NESTED_TREE})

    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    # startup also seeds RS1's parent strategy (ensure_parent_strategies), so
    # the list holds that plus the one created here
    rows = {r["name"]: r for r in resp.get_json()}
    assert set(rows) == {"RS1 (parent)", "Alpha"}
    assert "tree" not in rows["Alpha"]


def test_update_strategy_replaces_metadata_and_tree(client):
    created = client.post("/api/strategies", json={
        "name": "Original", "strategy_type": "entry", "tree": NESTED_TREE,
    }).get_json()

    new_tree = {"operator": "AND", "conditions": [{"element_code": "double_top"}], "groups": []}
    resp = client.put(f"/api/strategies/{created['id']}", json={"name": "Renamed", "tree": new_tree})
    assert resp.status_code == 200
    updated = resp.get_json()
    assert updated["name"] == "Renamed"
    assert updated["tree"]["conditions"][0]["element_code"] == "double_top"


def test_update_unknown_strategy_returns_404(client):
    resp = client.put("/api/strategies/9999", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_strategy_removes_it(client):
    created = client.post("/api/strategies", json={
        "name": "ToDelete", "strategy_type": "entry", "tree": NESTED_TREE,
    }).get_json()

    resp = client.delete(f"/api/strategies/{created['id']}")
    assert resp.status_code == 204
    assert client.get(f"/api/strategies/{created['id']}").status_code == 404


def test_delete_unknown_strategy_returns_404(client):
    resp = client.delete("/api/strategies/9999")
    assert resp.status_code == 404


def test_create_strategy_without_a_tree_is_allowed(client):
    resp = client.post("/api/strategies", json={"name": "Empty", "strategy_type": "entry"})
    assert resp.status_code == 201
    assert resp.get_json()["tree"] is None


def test_create_strategy_with_order_management_fields_round_trips(client):
    resp = client.post("/api/strategies", json={
        "name": "Order mgmt", "strategy_type": "entry",
        "sl_formula_type": "fixed_percent", "sl_fixed_value": 0.004,
        "target_formula_type": "fixed_percent", "target_fixed_value": 0.008,
        "capital_per_trade": 2_000_000.0, "max_vol_per_call": 5000,
        "max_orders_at_a_time": 3, "exit_at_loss_count": 1,
        "first_order_quantity": 1,
        "order1_margin_multiplier": 1.0, "order2_margin_multiplier": 1.0, "order3_margin_multiplier": 5.0,
        "trading_start_time": "09:15", "new_order_end_time": "14:00", "trading_end_time": "14:50",
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["sl_fixed_value"] == 0.004
    assert body["capital_per_trade"] == 2_000_000.0
    assert body["max_orders_at_a_time"] == 3
    assert body["first_order_quantity"] == 1
    assert body["order3_margin_multiplier"] == 5.0
    assert body["trading_start_time"] == "09:15"
    assert body["new_order_end_time"] == "14:00"
    assert body["trading_end_time"] == "14:50"

    get_resp = client.get(f"/api/strategies/{body['id']}")
    assert get_resp.get_json()["trading_start_time"] == "09:15"


def test_create_strategy_with_formula_leaves_round_trips(client):
    tree = {"operator": "AND", "groups": [], "conditions": [
        {"left_formula": "mean(volume, 5)", "operator": ">", "right_formula": "order_size"},
        {"left_formula": "rsi < 40"},
    ]}
    resp = client.post("/api/strategies", json={"name": "F", "strategy_type": "entry", "tree": tree})
    assert resp.status_code == 201
    stored = client.get(f"/api/strategies/{resp.get_json()['id']}").get_json()["tree"]["conditions"]
    assert [c["left_formula"] for c in stored] == ["mean(volume, 5)", "rsi < 40"]


def test_create_strategy_rejects_a_bad_formula(client):
    for bad in ({"left_formula": "nope > 1"}, {"left_formula": "rsi", "operator": ">"},
                {"left_formula": "abs(close) > 1"}, {"left_formula": "rsi + 1"}):
        tree = {"operator": "AND", "groups": [], "conditions": [bad]}
        resp = client.post("/api/strategies", json={"name": "F", "strategy_type": "entry", "tree": tree})
        assert resp.status_code == 400, bad


def test_strategy_fields_endpoint_serves_the_grammar(client):
    body = client.get("/api/strategy-fields").get_json()
    values = {f["value"] for f in body["fields"]}
    assert {"close", "volume", "rsi", "bb_upper"} <= values
    assert next(f for f in body["fields"] if f["value"] == "stoch_k")["aliases"] == ["sk"]
    assert {f["name"] for f in body["functions"]} == {"slope", "mean"}
    assert "order_size" in body["constants"]


def test_create_strategy_with_volume_gate_fields_round_trips(client):
    """min_avg_volume_multiple/min_avg_volume_lookback -- added 2026-09-18
    for the "mean volume[last N candles] > order size" entry gate."""
    resp = client.post("/api/strategies", json={
        "name": "Volume gate", "strategy_type": "entry",
        "min_avg_volume_multiple": 1.5, "min_avg_volume_lookback": 8,
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["min_avg_volume_multiple"] == 1.5
    assert body["min_avg_volume_lookback"] == 8

    get_resp = client.get(f"/api/strategies/{body['id']}")
    assert get_resp.get_json()["min_avg_volume_multiple"] == 1.5


def test_update_strategy_order_management_fields(client):
    created = client.post("/api/strategies", json={"name": "To update", "strategy_type": "entry"}).get_json()
    assert created["max_vol_per_call"] is None

    resp = client.put(f"/api/strategies/{created['id']}", json={
        "name": created["name"], "max_vol_per_call": 20000, "trading_start_time": "10:15",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["max_vol_per_call"] == 20000
    assert body["trading_start_time"] == "10:15"
