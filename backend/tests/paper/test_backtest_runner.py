from datetime import date, datetime, timedelta, timezone

from brokers.models import Candle
from paper.backtest_runner import summarize, trade_run
from paper.trade_logic import PaperSettings

T0 = datetime(2026, 11, 9, 4, 30)  # 10:00 IST
S = PaperSettings(stop_mode="points", stop_value=1, target_mode="points", target_value=2, slippage_pct=0)


def _candles(prices):
    return [Candle(symbol="ABC", timeframe="1min", timestamp=(T0 + timedelta(minutes=i)).replace(tzinfo=timezone.utc),
                   open=p, high=p + 0.1, low=p - 0.1, close=p, volume=0) for i, p in enumerate(prices)]


def _hit(minute, key="p1", direction="buy", close=100.0):
    return {"key": key, "timeframe": "1min", "ts": T0 + timedelta(minutes=minute), "close": close,
            "match": 1.0, "atr": {(14, "hit"): 1.0}, "direction": direction}


def test_trade_run_target_busy_and_other_run_keys():
    candles = _candles([100, 100.5, 101, 102.5, 100, 100])
    trades = trade_run([_hit(0), _hit(1), _hit(2, key="other")], candles, S, {"p1"})
    assert [t["status"] for t in trades] == ["closed", "not_taken"]  # "other" belongs to another run
    assert trades[0]["exit_reason"] == "target" and trades[0]["exit_price"] == 102.5  # opened past target: fills at open


def test_trade_run_closes_at_data_end():
    trades = trade_run([_hit(0)], _candles([100, 100.2, 100.3]), S, {"p1"})
    assert trades[0]["exit_reason"] == "data_end" and trades[0]["exit_price"] == 100.3


def test_summarize_halves_drawdown_and_stocks():
    base = {"status": "closed", "symbol": "A", "exit_reason": "stop"}
    trades = [dict(base, net_pnl=-100, net_pct=-0.1, hit_ts=datetime(2025, 1, 5), exit_ts=datetime(2025, 1, 5)),
              dict(base, net_pnl=-50, net_pct=-0.05, hit_ts=datetime(2025, 2, 5), exit_ts=datetime(2025, 2, 5)),
              dict(base, symbol="B", net_pnl=300, net_pct=0.3, exit_reason="target",
                   hit_ts=datetime(2025, 11, 5), exit_ts=datetime(2025, 11, 5)),
              {"status": "not_taken", "symbol": "A"}]
    s = summarize(trades, date(2025, 1, 1), date(2025, 12, 31))
    assert (s["trades"], s["not_taken"], s["net_pnl"], s["max_drawdown"]) == (3, 1, 150, 150)
    assert s["worst_losing_streak"] == 2 and (s["stocks_traded"], s["stocks_positive"]) == (2, 1)
    assert s["first_half"]["trades"] == 2 and s["second_half"]["net_pnl"] == 300
    assert s["exits"]["target"] == 1
