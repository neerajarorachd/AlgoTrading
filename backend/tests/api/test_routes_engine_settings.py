from activity_engine import ENGINE_SETTING_DEFAULTS


def test_list_returns_one_row_per_known_key_with_module_defaults(client):
    body = client.get("/api/engine-settings").get_json()
    by_key = {row["key"]: row for row in body}
    assert set(by_key) == set(ENGINE_SETTING_DEFAULTS)
    row = by_key["swing_lookback"]
    assert row["value"] == ENGINE_SETTING_DEFAULTS["swing_lookback"]
    assert row["default_value"] == ENGINE_SETTING_DEFAULTS["swing_lookback"]
    assert row["is_override"] is False
    assert row["description"]  # non-empty -- came from ENGINE_SETTING_DESCRIPTIONS


def test_put_promotes_a_backtest_proven_value_for_live(client):
    resp = client.put("/api/engine-settings/swing_lookback", json={"value": 7})
    assert resp.status_code == 200

    body = client.get("/api/engine-settings").get_json()
    row = next(r for r in body if r["key"] == "swing_lookback")
    assert row["value"] == 7.0 and row["is_override"] is True
    assert row["default_value"] == ENGINE_SETTING_DEFAULTS["swing_lookback"]  # unchanged


def test_put_unknown_key_is_rejected(client):
    resp = client.put("/api/engine-settings/not_a_real_key", json={"value": 1})
    assert resp.status_code == 400


def test_put_without_a_value_is_rejected(client):
    resp = client.put("/api/engine-settings/swing_lookback", json={})
    assert resp.status_code == 400


def test_put_non_numeric_value_is_rejected(client):
    resp = client.put("/api/engine-settings/swing_lookback", json={"value": "not a number"})
    assert resp.status_code == 400


def test_delete_reverts_to_the_module_default(client):
    client.put("/api/engine-settings/swing_lookback", json={"value": 7})
    resp = client.delete("/api/engine-settings/swing_lookback")
    assert resp.status_code == 200

    body = client.get("/api/engine-settings").get_json()
    row = next(r for r in body if r["key"] == "swing_lookback")
    assert row["is_override"] is False
    assert row["value"] == ENGINE_SETTING_DEFAULTS["swing_lookback"]


def test_delete_unknown_key_is_rejected(client):
    resp = client.delete("/api/engine-settings/not_a_real_key")
    assert resp.status_code == 400
