from datetime import datetime

import pytest

from trades import rules

T0 = datetime(2026, 11, 11, 4, 30)  # 10:00 IST


def _trade(direction="buy", entry_type="market", product="intraday", **kw):
    t = {"direction": direction, "quantity": 10, "entry_type": entry_type, "product": product,
         "slippage_pct": 0.05, "status": "pending", "limit_price": None, "stop_price": None,
         "target_price": None, "best_price": None, "worst_price": None}
    t.update(kw)
    return t


def test_buy_stop_and_target_on_ticks():
    t = rules.fill_entry(_trade(stop_price=98, target_price=105), ts=T0, price=100)
    assert rules.on_price(t, ts=T0, price=101) is None and t["best_price"] == 101
    assert rules.on_price(t, ts=T0, price=97.5) == "closed"
    assert t["exit_reason"] == "stop" and t["exit_price"] == 97.5
    assert t["gross_pnl"] == -25.0 and t["net_pnl"] < t["gross_pnl"]
    assert t["best_price"] == 101 and t["worst_price"] == 97.5


def test_sell_target_hit():
    t = rules.fill_entry(_trade("sell", stop_price=102, target_price=95), ts=T0, price=100)
    assert rules.on_price(t, ts=T0, price=94.8) == "closed"
    assert t["exit_reason"] == "target" and t["gross_pnl"] == pytest.approx(52.0)


def test_slippage_is_an_expense_not_in_the_price():
    t = rules.fill_entry(_trade(), ts=T0, price=100)
    rules.close_trade(t, ts=T0, price=110, reason="manual")
    assert t["entry_price"] == 100 and t["exit_price"] == 110 and t["gross_pnl"] == 100.0
    charges = rules.charges("intraday", 10, 110, 100)
    slip = (100 + 110) * 10 * 0.0005
    assert t["costs"] == pytest.approx(charges + slip, abs=0.01)
    # a limit entry and a target exit pay no slippage
    t2 = rules.fill_entry(_trade(entry_type="limit", limit_price=100), ts=T0, price=100)
    rules.close_trade(t2, ts=T0, price=110, reason="target")
    assert t2["costs"] == pytest.approx(charges, abs=0.01)


def test_delivery_costs_more_than_intraday():
    assert rules.charges("delivery", 100, 1000, 1000) > rules.charges("intraday", 100, 1000, 1000)


def test_limit_order_waits_then_fills():
    t = _trade(entry_type="limit", limit_price=99, stop_price=97)
    assert rules.on_price(t, ts=T0, price=100) is None and t["status"] == "pending"
    assert rules.on_price(t, ts=T0, price=98.9) == "filled"
    assert t["status"] == "open" and t["entry_price"] == 98.9


def test_intraday_square_off_and_pending_cancel_at_1510():
    late = datetime(2026, 11, 11, 9, 40)  # 15:10 IST
    t = rules.fill_entry(_trade(), ts=T0, price=100)
    assert rules.on_price(t, ts=late, price=101) == "closed" and t["exit_reason"] == "day_end"
    p = _trade(entry_type="limit", limit_price=90)
    assert rules.on_price(p, ts=late, price=101) == "closed" and p["status"] == "cancelled"
    d = rules.fill_entry(_trade(product="delivery"), ts=T0, price=100)
    assert rules.on_price(d, ts=late, price=101) is None and d["status"] == "open"


def test_candle_catch_up_stop_first_and_gaps():
    t = rules.fill_entry(_trade(stop_price=98, target_price=105), ts=T0, price=100)
    assert rules.on_candle(t, ts=T0, open_=100, high=106, low=97, close=101) == "closed"
    assert t["exit_reason"] == "stop" and t["exit_price"] == 98
    g = rules.fill_entry(_trade(stop_price=98, target_price=105), ts=T0, price=100)
    rules.on_candle(g, ts=T0, open_=96, high=97, low=95, close=96)
    assert g["exit_price"] == 96  # gapped through the stop: fills at the open
    p = _trade(entry_type="limit", limit_price=99)
    assert rules.on_candle(p, ts=T0, open_=100, high=100, low=98.5, close=99.5) == "filled"
    assert p["entry_price"] == 99


def test_level_and_window_checks():
    assert rules.validate_levels("buy", 100, 101, None)
    assert rules.validate_levels("sell", 100, None, 101)
    assert rules.validate_levels("buy", 100, 99, 101) is None
    assert rules.entry_window_error("intraday", datetime(2026, 11, 11, 3, 40))   # 09:10 IST
    assert rules.entry_window_error("intraday", datetime(2026, 11, 11, 9, 45))   # 15:15 IST
    assert rules.entry_window_error("delivery", datetime(2026, 11, 11, 9, 45)) is None
