def _condition(element_code, **overrides):
    """Full-key condition dict — matches exactly what GET /api/strategies/:id
    always returns (every optional field present, None where unset), so
    equality comparisons against a response body are apples-to-apples."""
    base = {
        "element_code": element_code, "operator": None, "compare_type": None,
        "compared_element_code": None, "static_value": None,
        "static_value_min": None, "static_value_max": None, "static_value_step": None,
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
    rows = resp.get_json()
    assert len(rows) == 1
    assert rows[0]["name"] == "Alpha"
    assert "tree" not in rows[0]


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
