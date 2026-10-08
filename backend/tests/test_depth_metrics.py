from depth_metrics import calculate_depth_metrics


def test_calculate_depth_metrics_from_full_depth():
    metrics = calculate_depth_metrics(
        ltp=100.0,
        bid_depth=[
            {"price": 99.5, "quantity": 100, "orders": 2},
            {"price": 99.0, "quantity": 300, "orders": 4},
        ],
        ask_depth=[
            {"price": 100.5, "quantity": 200, "orders": 3},
            {"price": 101.0, "quantity": 100, "orders": 2},
        ],
    )

    assert metrics["best_bid"] == 99.5
    assert metrics["best_ask"] == 100.5
    assert metrics["nearest_bid_percentage"] == 0.5
    assert metrics["nearest_ask_percentage"] == 0.5
    assert metrics["spread_percentage"] == 1.0
    assert metrics["maximum_bid_price"] == 99.0
    assert metrics["maximum_bid_quantity"] == 300
    assert metrics["maximum_bid_percentage"] == 75.0
    assert metrics["maximum_ask_price"] == 100.5
    assert metrics["maximum_ask_quantity"] == 200
    assert metrics["maximum_ask_percentage"] == 66.67
    assert metrics["bid_pressure_percentage"] == 57.14
    assert metrics["ask_pressure_percentage"] == 42.86


def test_calculate_depth_metrics_ignores_dhan_zero_padded_levels():
    # Dhan's Full-packet depth block always sends exactly 5 levels per side,
    # zero-padded (price=0, quantity=0) when fewer than 5 real orders exist
    # -- a thin stock commonly has only 1-2 real levels. A padding level must
    # not be treated as a real ₹0 bid/ask (real bug found live 2026-10-05:
    # it corrupted best_bid to 0 and nearest_bid_percentage to 100).
    metrics = calculate_depth_metrics(
        ltp=152.60,
        bid_depth=[
            {"price": 152.55, "quantity": 100, "orders": 1},
            {"price": 0.0, "quantity": 0, "orders": 0},
            {"price": 0.0, "quantity": 0, "orders": 0},
            {"price": 0.0, "quantity": 0, "orders": 0},
            {"price": 0.0, "quantity": 0, "orders": 0},
        ],
        ask_depth=[
            {"price": 152.65, "quantity": 50, "orders": 1},
            {"price": 0.0, "quantity": 0, "orders": 0},
            {"price": 0.0, "quantity": 0, "orders": 0},
            {"price": 0.0, "quantity": 0, "orders": 0},
            {"price": 0.0, "quantity": 0, "orders": 0},
        ],
    )

    assert metrics["best_bid"] == 152.55
    assert metrics["best_ask"] == 152.65
    assert metrics["nearest_bid_percentage"] != 100  # was the corrupted value before the fix
    assert metrics["total_bid_quantity"] == 100
    assert metrics["total_ask_quantity"] == 50
    assert metrics["bid_pressure_percentage"] == 66.67


def test_calculate_depth_metrics_handles_an_entirely_zero_padded_side():
    # Both sides fully padded (no real orders at all) must fall back to
    # None/0.0, not propagate zeros as if they were real prices.
    metrics = calculate_depth_metrics(
        ltp=100.0,
        bid_depth=[{"price": 0.0, "quantity": 0, "orders": 0}] * 5,
        ask_depth=[{"price": 0.0, "quantity": 0, "orders": 0}] * 5,
    )

    assert metrics["best_bid"] is None
    assert metrics["best_ask"] is None
    assert metrics["nearest_bid_percentage"] == 0.0
    assert metrics["nearest_ask_percentage"] == 0.0
    assert metrics["spread_percentage"] == 0.0
    assert metrics["bid_pressure_percentage"] == 0.0
    assert metrics["ask_pressure_percentage"] == 0.0