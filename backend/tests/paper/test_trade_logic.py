from datetime import datetime

from paper.trade_logic import PaperSettings, close_trade, ist_hhmm, on_candle, open_trade

S = PaperSettings(slippage_pct=0.0)
HIT = datetime(2026, 11, 9, 4, 30)  # 10:00 IST


def _open(direction="buy", close=100.0, atr=1.0, settings=S, **kw):
    return open_trade(hit_ts=HIT, direction=direction, hit_close=close, atr=atr, settings=settings, **kw)


def test_ist_hhmm():
    assert ist_hhmm(HIT) == 1000


def test_open_buy_levels_and_quantity():
    t = _open()
    assert t["status"] == "open"
    assert (t["entry_price"], t["quantity"], t["stop_price"], t["target_price"]) == (100.0, 1000, 98.0, 110.0)


def test_open_sell_levels_with_slippage():
    t = _open("sell", settings=PaperSettings())
    assert t["entry_price"] == 99.95  # 0.05% against us
    assert t["stop_price"] == 101.95 and t["target_price"] == 89.95


def test_not_taken_reasons_are_recorded():
    late = open_trade(hit_ts=datetime(2026, 11, 9, 9, 20), direction="buy", hit_close=100, atr=1, settings=S)
    assert late["status"] == "not_taken" and "entry window" in late["not_taken_reason"]
    assert "already" in _open(already_open=True)["not_taken_reason"]
    assert "ATR" in _open(atr=None)["not_taken_reason"]
    assert "amount" in _open(close=200000.0)["not_taken_reason"]


def test_stop_wins_when_both_touched():
    t = _open()
    assert on_candle(t, ts=datetime(2026, 11, 9, 4, 31), open_=100, high=111, low=97, close=100, settings=S)
    assert t["exit_reason"] == "stop" and t["exit_price"] == 98.0
    assert t["gross_pnl"] == -2000.0 and t["net_pnl"] < t["gross_pnl"]


def test_target_and_gap_fill_at_open():
    t = _open()
    assert not on_candle(t, ts=datetime(2026, 11, 9, 4, 31), open_=100, high=101, low=99.5, close=100.5, settings=S)
    assert on_candle(t, ts=datetime(2026, 11, 9, 4, 32), open_=112, high=113, low=111, close=112, settings=S)
    assert t["exit_reason"] == "target" and t["exit_price"] == 112  # gapped past target: fill at open
    assert t["best_pct"] == 13.0 and t["worst_pct"] == -0.5  # includes the exit candle


def test_day_end_square_off():
    t = _open("sell")
    assert on_candle(t, ts=datetime(2026, 11, 9, 9, 40), open_=99, high=99.5, low=98.5, close=99, settings=S)
    assert t["exit_reason"] == "day_end" and t["exit_price"] == 99 and t["gross_pnl"] == 1000.0


def test_closed_trade_ignores_more_candles():
    t = close_trade(_open(), exit_ts=HIT, exit_price=101, reason="target")
    assert not on_candle(t, ts=datetime(2026, 11, 9, 4, 31), open_=90, high=90, low=80, close=85, settings=S)
