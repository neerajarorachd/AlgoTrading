def test_subscribed_room_receives_broadcast_tick(client, app, fake_broker):
    socketio = app.extensions["socketio"]
    market_feed = app.extensions["market_feed"]

    client.post("/api/symbols", json={"symbol": "RELIANCE", "exchange": "NSE", "segment": "EQUITY"})

    ws_client = socketio.test_client(app, flask_test_client=client)
    ws_client.emit("subscribe_ticks", {"rooms": ["NSE:RELIANCE"]})
    ws_client.get_received()  # drain any connect-time noise

    fake_broker.callback({
        "SecurityId": "SEC-RELIANCE", "ExchangeSegment": "NSE_EQ",
        "LTP": 2854.65, "PreviousClose": 2828.4,
        "Timestamp": "2026-09-10T09:16:02Z",
    })

    received = ws_client.get_received()
    tick_events = [msg for msg in received if msg["name"] == "tick"]
    assert len(tick_events) == 1
    payload = tick_events[0]["args"][0]
    assert payload["symbol"] == "RELIANCE"
    assert payload["ltp"] == 2854.65

    ws_client.disconnect()


def test_unsubscribed_room_receives_nothing(client, app, fake_broker):
    socketio = app.extensions["socketio"]
    client.post("/api/symbols", json={"symbol": "TCS", "exchange": "NSE", "segment": "EQUITY"})

    ws_client = socketio.test_client(app, flask_test_client=client)
    ws_client.get_received()  # no subscribe_ticks call at all

    fake_broker.callback({
        "SecurityId": "SEC-TCS", "ExchangeSegment": "NSE_EQ",
        "LTP": 3500.0, "PreviousClose": 3480.0,
        "Timestamp": "2026-09-10T09:16:02Z",
    })

    received = ws_client.get_received()
    assert [msg for msg in received if msg["name"] == "tick"] == []

    ws_client.disconnect()
