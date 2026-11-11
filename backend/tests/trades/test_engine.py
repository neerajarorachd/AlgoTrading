from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from db.models import CandleToday
from db.models_trades import Trade, TradeIndicatorSnapshot
from trades.engine import OrderError, TradeEngine

NOW = datetime(2026, 11, 11, 4, 30)  # 10:00 IST
INST = SimpleNamespace(id=7, symbol="RELIANCE", exchange_segment="NSE_EQ")


class FakeActivityEngine:
    pattern_evaluator = None

    def recent_rows(self, symbol, seg, tf, n):
        rows = [{"ts": NOW - timedelta(minutes=i), "open": 100, "high": 101, "low": 99, "close": 100,
                 "volume": 1000 + i, "rsi": 55.0, "macd_line": 1.2, "macd_signal": 1.0, "bb_upper": 102.0,
                 "bb_middle": 100.0, "bb_lower": 98.0, "adx": 25.0} for i in range(n, 0, -1)]
        return rows


def _engine(session_factory, price=100.0, now=NOW):
    prices = {"p": price}
    eng = TradeEngine(session_factory, FakeActivityEngine(), price_lookup=lambda s, e: prices["p"],
                      now_fn=lambda: now)
    return eng, prices


def test_market_order_fills_and_snapshots_three_timeframes(session_factory):
    eng, _ = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=10, stop_price=98, target_price=105,
                        screen_signal="Hold")
    assert t["status"] == "open" and t["entry_price"] == 100 and t["running"]["gross_pnl"] == 0
    assert t["expiry_date"].isoformat() == "2026-11-23" and t["days_to_expiry"] == 8
    assert ("RELIANCE", "NSE_EQ") in eng.watched
    with session_factory() as s:
        snaps = s.query(TradeIndicatorSnapshot).filter_by(trade_id=t["id"], event="entry").all()
        assert sorted(x.timeframe for x in snaps) == ["1min", "3min", "5min"]
        assert snaps[0].rsi == 55.0 and snaps[0].macd_hist == pytest.approx(0.2) and snaps[0].bb_width_pct == 4.0
        assert snaps[0].avg_volume_20 is not None


def test_queue_path_closes_at_target_and_frees_capital(session_factory):
    eng, prices = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=10, target_price=105)
    eng.offer_price("TCS", "NSE_EQ", 1, NOW)               # not watched: ignored
    assert eng._queue.empty()
    eng.offer_price("RELIANCE", "NSE_EQ", 105.5, NOW)
    key, price, ts = eng._queue.get_nowait()
    eng.process_price(key[0], key[1], price, ts)
    assert ("RELIANCE", "NSE_EQ") not in eng.watched
    with session_factory() as s:
        row = s.get(Trade, t["id"])
        assert row.status == "closed" and row.exit_reason == "target" and row.gross_pnl == 55.0
        assert s.query(TradeIndicatorSnapshot).filter_by(trade_id=t["id"], event="exit").count() == 3
    acct = eng.account(1)
    assert acct["used"] == 0 and acct["realised_pnl"] == row.net_pnl
    assert acct["realised_today_pnl"] == row.net_pnl and acct["realised_today_gross"] == 55.0
    assert acct["free"] == pytest.approx(500000 + row.net_pnl)


def test_refusals(session_factory):
    eng, _ = _engine(session_factory)
    with pytest.raises(OrderError, match="coming soon"):
        eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=1, mode="live")
    with pytest.raises(OrderError, match="short"):
        eng.place_order(owner_id=1, instrument=INST, direction="sell", quantity=1, product="delivery")
    with pytest.raises(OrderError, match="stop must be below"):
        eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=1, stop_price=101)
    with pytest.raises(OrderError, match="capital"):
        eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=6000)  # Rs 6L > 5L
    late, _ = _engine(session_factory, now=datetime(2026, 11, 11, 9, 45))
    with pytest.raises(OrderError, match="09:15"):
        late.place_order(owner_id=1, instrument=INST, direction="buy", quantity=1)


def test_limit_modify_exit_and_cancel(session_factory):
    eng, prices = _engine(session_factory)
    p = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=5, entry_type="limit", limit_price=99)
    assert p["status"] == "pending"
    eng.process_price("RELIANCE", "NSE_EQ", 98.8, NOW)
    t = eng.active_trades(1)[0]
    assert t["status"] == "open" and t["entry_price"] == 98.8
    m = eng.modify(1, t["id"], stop_price=97, target_price=None)
    assert m["stop_price"] == 97 and m["target_price"] is None
    with pytest.raises(OrderError):
        eng.modify(1, t["id"], stop_price=150)
    prices["p"] = 99.5
    out = eng.exit_now(1, t["id"])
    assert out["status"] == "closed" and out["exit_reason"] == "manual" and out["exit_price"] == 99.5
    c = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=5, entry_type="limit", limit_price=90)
    assert eng.exit_now(1, c["id"])["status"] == "cancelled"
    assert eng.active_trades(1) == []


def test_day_end_square_off_by_timer(session_factory):
    eng, prices = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="sell", quantity=10)
    eng.check_day_end(now=datetime(2026, 11, 11, 9, 41))
    with session_factory() as s:
        row = s.get(Trade, t["id"])
        assert row.status == "closed" and row.exit_reason == "day_end"


def test_restart_catches_up_on_missed_candles(session_factory):
    eng, _ = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=10, stop_price=98)
    with session_factory() as s:
        for i, low in enumerate((99.5, 97.0)):
            s.add(CandleToday(symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1min",
                              ts=NOW + timedelta(minutes=i + 1), open_price=100, high_price=100.5,
                              low_price=low, close_price=99, volume=100))
        s.commit()
    fresh, _ = _engine(session_factory, now=NOW + timedelta(minutes=5))
    assert fresh.load_active() == 0
    with session_factory() as s:
        row = s.get(Trade, t["id"])
        assert row.status == "closed" and row.exit_reason == "stop" and row.exit_price == 98


def test_limit_exit_waits_then_closes_without_slippage(session_factory):
    eng, prices = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=10)
    with pytest.raises(OrderError, match="limit price"):
        eng.exit_now(1, t["id"], "limit", None)
    w = eng.exit_now(1, t["id"], "limit", 102)
    assert w["status"] == "open" and w["exit_limit_price"] == 102
    eng.process_price("RELIANCE", "NSE_EQ", 101.5, NOW)
    assert eng.active_trades(1)[0]["status"] == "open"
    eng.process_price("RELIANCE", "NSE_EQ", 102.1, NOW)
    with session_factory() as s:
        row = s.get(Trade, t["id"])
        assert row.status == "closed" and row.exit_reason == "limit" and row.exit_price == 102.1
        # entry was market (slippage on entry only), the limit exit pays none
        from trades.rules import charges
        assert row.costs == pytest.approx(charges("intraday", 10, 102.1, 100) + 100 * 10 * 0.0005, abs=0.01)


def test_marketable_limit_exit_fills_at_once_and_cancel(session_factory):
    eng, prices = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="sell", quantity=5)
    w = eng.exit_now(1, t["id"], "limit", 95)          # sell closes by buying: waits for <= 95
    assert w["status"] == "open"
    assert eng.cancel_exit_limit(1, t["id"])["exit_limit_price"] is None
    out = eng.exit_now(1, t["id"], "limit", 101)        # price 100 <= 101: fills now at 100
    assert out["status"] == "closed" and out["exit_reason"] == "limit" and out["exit_price"] == 100


def test_limit_exit_on_restart_candles(session_factory):
    eng, _ = _engine(session_factory)
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=10)
    eng.exit_now(1, t["id"], "limit", 103)
    with session_factory() as s:
        s.add(CandleToday(symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1min", ts=NOW + timedelta(minutes=1),
                          open_price=101, high_price=103.5, low_price=100.5, close_price=103, volume=10))
        s.commit()
    fresh, _ = _engine(session_factory, now=NOW + timedelta(minutes=3))
    fresh.load_active()
    with session_factory() as s:
        row = s.get(Trade, t["id"])
        assert row.exit_reason == "limit" and row.exit_price == 103


def test_two_exit_legs_block_double_capital(session_factory):
    eng, _ = _engine(session_factory)
    eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=1000, stop_price=98)  # Rs 1L, one leg
    assert eng.account(1)["used"] == 100000
    t = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=1000, stop_price=98, target_price=105)
    assert eng.account(1)["used"] == 100000 + 200000          # both legs: 2x
    eng.modify(1, t["id"], target_price=None)                 # back to one leg: 1x
    assert eng.account(1)["used"] == 200000
    # free 3L: a 1.6L trade with both legs needs 3.2L -> refused; one leg is fine
    with pytest.raises(OrderError, match="capital"):
        eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=1600, stop_price=98, target_price=105)
    big = eng.place_order(owner_id=1, instrument=INST, direction="buy", quantity=1600, stop_price=98)
    with pytest.raises(OrderError, match="second exit leg"):
        eng.modify(1, big["id"], target_price=105)
