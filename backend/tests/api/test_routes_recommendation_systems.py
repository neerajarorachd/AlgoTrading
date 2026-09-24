from rule_gate import summarize_tree


def _rs1(client):
    return next(r for r in client.get("/api/recommendation-systems").get_json() if r["code"] == "RS1")


def _rule(client, parent_id, name, patterns, formula):
    return client.post("/api/strategies", json={
        "name": name, "strategy_type": "recommendation_rule", "parent_id": parent_id,
        "pattern_filter": patterns,
        "tree": {"operator": "AND", "groups": [], "conditions": [{"left_formula": formula}]},
    }).get_json()["id"]


def test_lists_rs1_with_its_parent_strategy(client):
    rs1 = _rs1(client)
    assert rs1["strategy_id"] is not None and rs1["rule_combine_mode"] == "all" and rs1["is_active"] is True


def test_detail_lists_child_rules_with_their_patterns_and_summary(client):
    rs1 = _rs1(client)
    _rule(client, rs1["strategy_id"], "Rule 1", "A,B, C", "rsi < 40")
    _rule(client, rs1["strategy_id"], "Rule 2", "C", "volume > 100")
    client.post("/api/strategies", json={"name": "not a child", "strategy_type": "entry"})

    detail = client.get(f"/api/recommendation-systems/{rs1['id']}").get_json()
    assert [r["name"] for r in detail["rules"]] == ["Rule 1", "Rule 2"]
    assert detail["rules"][0]["patterns"] == ["A", "B", "C"]
    assert detail["rules"][0]["summary"] == "rsi < 40"


def test_update_settings_and_combine_mode(client):
    rs1 = _rs1(client)
    resp = client.put(f"/api/recommendation-systems/{rs1['id']}", json={
        "timeframe": "5min", "top_n_per_run": 2, "rule_combine_mode": "any",
        "min_sample_size_override": 7, "win_pct_threshold_override": 0.65, "is_active": False,
    })
    assert resp.status_code == 200
    body = client.get(f"/api/recommendation-systems/{rs1['id']}").get_json()
    assert (body["timeframe"], body["top_n_per_run"], body["rule_combine_mode"]) == ("5min", 2, "any")
    assert body["min_sample_size_override"] == 7 and body["win_pct_threshold_override"] == 0.65
    assert body["is_active"] is False


def test_update_rejects_bad_values(client):
    rs_id = _rs1(client)["id"]
    for bad in ({"timeframe": "7min"}, {"rule_combine_mode": "some"}, {"top_n_per_run": 0}, {"name": " "}):
        assert client.put(f"/api/recommendation-systems/{rs_id}", json=bad).status_code == 400, bad


def test_unknown_system_is_404(client):
    assert client.get("/api/recommendation-systems/9999").status_code == 404
    assert client.put("/api/recommendation-systems/9999", json={}).status_code == 404


def test_summarize_tree_nests_groups_and_handles_operatorless_leaves():
    tree = {"operator": "AND", "conditions": [
        {"left_formula": "rsi", "operator": "<", "right_formula": "40"}, {"left_formula": "close > vwap"}],
        "groups": [{"operator": "OR", "groups": [], "conditions": [
            {"left_formula": "volume", "operator": ">", "right_formula": "5"},
            {"left_formula": "atr", "operator": ">", "right_formula": "1"}]}]}
    assert summarize_tree(tree) == "rsi < 40 AND close > vwap AND (volume > 5 OR atr > 1)"
