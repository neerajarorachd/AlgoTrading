from brokers.dhan_broker import DhanBroker


def test_feed_status_reports_unavailable_for_a_broker_without_health_reporting(client):
    # conftest's fake broker has no feed_status()
    body = client.get("/api/feed/status").get_json()
    assert body == {"available": False}


def test_feed_status_reports_the_dhan_brokers_live_state(app, client):
    broker = DhanBroker(client_id="c1", access_token="t1", ws_client_factory=lambda on_tick: _NullWS())
    broker.subscribe_feed([{"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE"}], on_tick=lambda t: None)
    broker._on_ws_open()
    app.extensions["feed_broker"] = broker

    body = client.get("/api/feed/status").get_json()
    assert body["available"] is True
    assert body["connected"] is True
    assert body["subscribed"] == 1
    assert body["disconnects"] == 0


class _NullWS:
    def connect(self, url):
        pass

    def send(self, message):
        pass

    def close(self):
        pass
