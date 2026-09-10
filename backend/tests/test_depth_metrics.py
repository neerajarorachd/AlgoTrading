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