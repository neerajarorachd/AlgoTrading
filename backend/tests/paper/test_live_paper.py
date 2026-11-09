from datetime import datetime

from brokers.models import Candle
from db.models import CandleToday
from db.models_paper import PaperSelection, PaperTradeLive
from paper.live_paper import LivePaperTrader


class FakeEvaluator:
    def meta(self, key):
        return (key, "bullish")


class FakeEngine:
    pattern_evaluator = FakeEvaluator()

    def recent_rows(self, symbol, seg, tf, n):
        return [{"high": 101, "low": 99, "close": 100}] * n  # ATR = 2


def _hit(key="p1", ts=datetime(2026, 11, 9, 4, 30), close=100.0):
    return {"instrument_id": 7, "timeframe": "5min", "ts": ts, "activity_type": "pattern",
            "activity": key, "intensity": 4.0, "close_price": close}


def _c(tf, ts, o, h, l, c):
    return Candle(symbol="ABC", timeframe=tf, timestamp=ts, open=o, high=h, low=l, close=c, volume=0)


def test_selected_hit_opens_then_target_closes(session_factory):
    with session_factory() as s:
        s.add(PaperSelection(owner_id=1, item_key="p1", target_mode="points", target_value=3, slippage_pct=0))
        s.commit()
    pt = LivePaperTrader(session_factory, FakeEngine())
    hit_candle = _c("5min", datetime(2026, 11, 9, 4, 30), 100, 100, 100, 100)
    pt.on_candle_closed("ABC", "NSE_EQ", hit_candle, [_hit(), _hit("unselected")])
    with session_factory() as s:
        rows = s.query(PaperTradeLive).all()
        assert len(rows) == 1 and rows[0].status == "open"
        assert (rows[0].stop_price, rows[0].target_price, rows[0].quantity) == (96.0, 103.0, 1000)
    # inside the hit candle: ignored; after it: target
    pt.on_candle_closed("ABC", "NSE_EQ", _c("1min", datetime(2026, 11, 9, 4, 33), 100, 104, 100, 103), [])
    pt.on_candle_closed("ABC", "NSE_EQ", _c("1min", datetime(2026, 11, 9, 4, 35), 100, 104, 100, 103), [])
    with session_factory() as s:
        row = s.query(PaperTradeLive).one()
        assert row.status == "closed" and row.exit_reason == "target" and row.exit_price == 103.0


def test_second_hit_while_open_is_recorded_not_taken(session_factory):
    with session_factory() as s:
        s.add(PaperSelection(owner_id=1, item_key="p1"))
        s.commit()
    pt = LivePaperTrader(session_factory, FakeEngine())
    c = _c("5min", datetime(2026, 11, 9, 4, 30), 100, 100, 100, 100)
    pt.on_candle_closed("ABC", "NSE_EQ", c, [_hit()])
    pt.on_candle_closed("ABC", "NSE_EQ", c, [_hit(ts=datetime(2026, 11, 9, 4, 35))])
    with session_factory() as s:
        st = sorted((r.status, r.not_taken_reason or "") for r in s.query(PaperTradeLive))
        assert st[0][0] == "not_taken" and "already" in st[0][1] and st[1][0] == "open"


def test_restart_reload_catches_up_missed_candles(session_factory):
    with session_factory() as s:
        s.add(PaperSelection(owner_id=1, item_key="p1", stop_mode="points", stop_value=1, slippage_pct=0))
        s.commit()
    pt = LivePaperTrader(session_factory, FakeEngine())
    pt.on_candle_closed("ABC", "NSE_EQ", _c("5min", datetime(2026, 11, 9, 4, 30), 100, 100, 100, 100), [_hit()])
    with session_factory() as s:  # a candle that arrived while the app was down
        s.add(CandleToday(symbol="ABC", exchange_segment="NSE_EQ", timeframe="1min", ts=datetime(2026, 11, 9, 4, 40),
                          open_price=100, high_price=100, low_price=98.5, close_price=99, volume=0))
        s.commit()
    assert LivePaperTrader(session_factory, FakeEngine()).load_open_trades(now=datetime(2026, 11, 9, 5, 0)) == 0
    with session_factory() as s:
        row = s.query(PaperTradeLive).one()
        assert row.exit_reason == "stop" and row.exit_price == 99.0
