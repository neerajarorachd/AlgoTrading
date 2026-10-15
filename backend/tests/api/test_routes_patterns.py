"""ALGO-31: pattern API -- the app seeds system patterns at startup; users
create, edit, copy and disable their own; system ones are read-only."""
from patterns_catalog import SYSTEM_PATTERNS


def _create(client, **over):
    body = {"key": "my_setup", "name": "My setup", "pattern_type": "bullish",
            "conditions": [{"formula": "RSI > 45", "note": "momentum"}, {"formula": "CLOSE > VWAP"}]}
    body.update(over)
    return client.post("/api/patterns", json=body)


def test_system_patterns_are_seeded_and_listed(client):
    patterns = client.get("/api/patterns").get_json()["patterns"]
    system = [p for p in patterns if p["origin"] == "system"]
    assert len(system) == len(SYSTEM_PATTERNS)
    assert all(p["conditions"] for p in system)


def test_create_get_update_copy_disable(client):
    resp = _create(client, manual_weight=4)
    assert resp.status_code == 201
    pid = resp.get_json()["id"]
    p = client.get(f"/api/patterns/{pid}").get_json()
    assert p["weight"] == 4 and p["conditions"][0]["note"] == "momentum"

    assert client.put(f"/api/patterns/{pid}", json={"name": "Renamed", "conditions": ["RSI > 50"]}).status_code == 200
    assert client.get(f"/api/patterns/{pid}").get_json()["conditions"][0]["formula"] == "RSI > 50"

    copy = client.post(f"/api/patterns/{pid}/copy").get_json()["id"]
    assert client.get(f"/api/patterns/{copy}").get_json()["parent_id"] == pid

    assert client.delete(f"/api/patterns/{pid}").status_code == 200
    assert client.get(f"/api/patterns/{pid}").get_json()["enabled"] is False
    enabled = client.get("/api/patterns?enabled=1").get_json()["patterns"]
    assert pid not in {p["id"] for p in enabled}


def test_invalid_formula_is_rejected_with_the_reason(client):
    resp = _create(client, conditions=["RECENTLY(RSI, 3)"])
    assert resp.status_code == 400
    assert "needs a condition" in resp.get_json()["error"]


def test_system_pattern_edit_is_forbidden_but_copy_works(client):
    sys_id = next(p["id"] for p in client.get("/api/patterns").get_json()["patterns"] if p["key"] == "strong_hammer")
    resp = client.put(f"/api/patterns/{sys_id}", json={"manual_weight": 5})
    assert resp.status_code == 403 and "copy" in resp.get_json()["error"]
    copy = client.post(f"/api/patterns/{sys_id}/copy")
    assert copy.status_code == 201
    assert client.put(f"/api/patterns/{copy.get_json()['id']}", json={"manual_weight": 5}).status_code == 200


def test_validate_condition_endpoint(client):
    assert client.post("/api/patterns/validate-condition", json={"formula": "RECENTLY(EVENT(hammer), 5)"}).get_json()["error"] is None
    assert "unknown" in client.post("/api/patterns/validate-condition", json={"formula": "FOO > 1"}).get_json()["error"]


def test_pattern_settings_default_off(client):
    body = client.get("/api/pattern-settings").get_json()
    assert body == {"use_per_stock_weightage": False, "pattern_formed_threshold": 0.8}


def test_unknown_pattern_is_404(client):
    assert client.get("/api/patterns/99999").status_code == 404
    assert client.put("/api/patterns/99999", json={"name": "x"}).status_code == 404


def test_condition_reckoner_is_served_from_the_doc(client):
    body = client.get("/api/condition-reckoner").get_json()
    assert body["markdown"].startswith("# Condition Maker: Ready Reckoner")
