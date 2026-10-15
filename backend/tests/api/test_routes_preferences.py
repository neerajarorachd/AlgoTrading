"""User preferences API (2026-10-15)."""


def test_empty_then_saved_values_come_back(client):
    assert client.get("/api/preferences").get_json() == {}
    r = client.put("/api/preferences", json={"workspace.screenTimeframe": "3min", "chart.toggles": {"events": True, "rsi": False}})
    assert r.status_code == 200
    assert client.get("/api/preferences").get_json() == {
        "workspace.screenTimeframe": "3min", "chart.toggles": {"events": True, "rsi": False},
    }


def test_put_merges_and_overwrites_and_null_removes(client):
    client.put("/api/preferences", json={"a": 1, "b": [1, 2]})
    client.put("/api/preferences", json={"a": 2, "b": None, "c": "x"})
    assert client.get("/api/preferences").get_json() == {"a": 2, "c": "x"}


def test_bad_bodies_are_rejected(client):
    assert client.put("/api/preferences", json=[1, 2]).status_code == 400
    assert client.put("/api/preferences", json={"k" * 101: 1}).status_code == 400
    assert client.put("/api/preferences", json={"big": "x" * 30000}).status_code == 400
