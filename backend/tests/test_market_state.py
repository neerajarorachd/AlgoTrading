from market_state import build_tick_payload


def test_build_tick_payload_for_up_move():
    payload = build_tick_payload(
        symbol="RELIANCE",
        exchange="NSE",
        segment="EQUITY",
        ltp=2854.65,
        previous_close=2828.4,
        currency="INR",
        ts="2026-09-10T09:16:02Z",
    )

    assert payload["type"] == "tick"
    assert payload["symbol"] == "RELIANCE"
    assert payload["exchange"] == "NSE"
    assert payload["segment"] == "EQUITY"
    assert payload["ltp"] == 2854.65
    assert payload["previous_close"] == 2828.4
    assert payload["currency"] == "INR"
    assert payload["absolute_change"] == 26.25
    assert payload["percentage_change"] == 0.93
    assert payload["direction"] == "up"
    assert payload["ts"] == "2026-09-10T09:16:02Z"


def test_build_tick_payload_for_down_move():
    payload = build_tick_payload(
        symbol="TCS",
        exchange="NSE",
        segment="EQUITY",
        ltp=3400.0,
        previous_close=3450.0,
        currency="INR",
        ts="2026-09-10T09:17:02Z",
    )

    assert payload["absolute_change"] == -50.0
    assert payload["percentage_change"] == -1.45
    assert payload["direction"] == "down"


def test_build_tick_payload_for_flat_move():
    payload = build_tick_payload(
        symbol="INFY",
        exchange="NSE",
        segment="EQUITY",
        ltp=1500.0,
        previous_close=1500.0,
        currency="INR",
        ts="2026-09-10T09:18:02Z",
    )

    assert payload["absolute_change"] == 0.0
    assert payload["percentage_change"] == 0.0
    assert payload["direction"] == "flat"
