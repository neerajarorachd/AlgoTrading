"""Order book behind each trade (rulebook 3b, 11-11): bracket legs + OCO."""
from datetime import datetime

from tests.trades.test_engine import INST, NOW, _engine


def _book(eng, tid):
    return {(o["kind"], o["order_type"]): o for o in eng.orders_for(1, trade_id=tid)}


def test_market_entry_creates_stop_and_target_legs(session_factory):
    eng, _ = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=10, stop_price=98, target_price=105)
    b = _book(eng, t["id"])
    assert b[("entry", "market")]["status"] == "filled" and b[("entry", "market")]["fill_price"] == 100
    assert b[("stop", "sl_m")]["status"] == "pending" and b[("stop", "sl_m")]["price"] == 98
    assert b[("target", "limit")]["status"] == "pending" and b[("target", "limit")]["side"] == "sell"


def test_stop_fills_and_cancels_target(session_factory):
    eng, _ = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=10, stop_price=98, target_price=105)
    eng.process_price("RELIANCE", "NSE_EQ", 97.9, NOW)
    b = _book(eng, t["id"])
    assert b[("stop", "sl_m")]["status"] == "filled" and b[("stop", "sl_m")]["fill_price"] == 97.9
    assert b[("target", "limit")]["status"] == "cancelled" and b[("target", "limit")]["cancel_reason"] == "OCO: stop filled"


def test_target_fills_and_cancels_stop_for_a_sell(session_factory):
    eng, _ = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="sell", quantity=5, stop_price=103, target_price=96)
    eng.process_price("RELIANCE", "NSE_EQ", 95.5, NOW)
    b = _book(eng, t["id"])
    assert b[("target", "limit")]["status"] == "filled" and b[("target", "limit")]["side"] == "buy"
    assert b[("stop", "sl_m")]["cancel_reason"] == "OCO: target filled"


def test_manual_exit_cancels_both_legs(session_factory):
    eng, _ = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=10, stop_price=98, target_price=105)
    eng.exit_now(1, t["id"])
    b = _book(eng, t["id"])
    assert b[("exit", "market")]["status"] == "filled" and b[("exit", "market")]["fill_price"] == 100
    assert b[("stop", "sl_m")]["cancel_reason"] == "manual exit" and b[("target", "limit")]["cancel_reason"] == "manual exit"


def test_limit_exit_replaces_target_keeps_stop(session_factory):
    eng, _ = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=10, stop_price=98, target_price=105)
    eng.exit_now(1, t["id"], "limit", 103)
    b = _book(eng, t["id"])
    assert b[("target", "limit")]["status"] == "cancelled" and b[("target", "limit")]["cancel_reason"] == "replaced by limit exit"
    assert b[("stop", "sl_m")]["status"] == "pending" and b[("exit", "limit")]["status"] == "pending"
    eng.process_price("RELIANCE", "NSE_EQ", 103.2, NOW)
    b = _book(eng, t["id"])
    assert b[("exit", "limit")]["status"] == "filled" and b[("stop", "sl_m")]["cancel_reason"] == "OCO: limit exit filled"


def test_limit_entry_legs_wait_for_the_fill(session_factory):
    eng, _ = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=5, entry_type="limit",
                        limit_price=99, stop_price=97, target_price=104)
    assert [o["kind"] for o in eng.orders_for(1, trade_id=t["id"])] == ["entry"]
    eng.process_price("RELIANCE", "NSE_EQ", 98.9, NOW)
    b = _book(eng, t["id"])
    assert b[("entry", "limit")]["status"] == "filled"
    assert b[("stop", "sl_m")]["status"] == "pending" and b[("target", "limit")]["status"] == "pending"


def test_cancel_single_orders_from_pending(session_factory):
    eng, _ = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=10, stop_price=98, target_price=105)
    stop_id = _book(eng, t["id"])[("stop", "sl_m")]["id"]
    out = eng.cancel_order(1, stop_id)
    assert out["status"] == "open" and out["stop_price"] is None
    b = _book(eng, t["id"])
    assert b[("stop", "sl_m")]["status"] == "cancelled" and b[("target", "limit")]["status"] == "pending"
    p = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=1, entry_type="limit", limit_price=90)
    entry_id = _book(eng, p["id"])[("entry", "limit")]["id"]
    assert eng.cancel_order(1, entry_id)["status"] == "cancelled"
    assert eng.orders_for(1, status="pending") and all(o["trade_id"] == t["id"] for o in eng.orders_for(1, status="pending"))


def test_day_end_square_off_order(session_factory):
    eng, _ = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=10, stop_price=98)
    eng.check_day_end(now=datetime(2026, 11, 11, 9, 41))
    b = _book(eng, t["id"])
    assert b[("exit", "market")]["status"] == "filled" and b[("stop", "sl_m")]["cancel_reason"] == "day-end square-off"
