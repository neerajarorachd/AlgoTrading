import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from backtest_historical import compute_trades, summarize  # noqa: E402

from db.models import CandleHistorical, PatternPrediction, SubscribedSymbol  # noqa: E402

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def _register(session_factory):
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="1333", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _prediction(session_factory, instrument_id, **overrides):
    kwargs = dict(
        instrument_id=instrument_id, timeframe="1day", pattern="hammer", direction="bull",
        detected_ts=_dt(2026, 1, 1), entry_price=100.0, neckline=None,
        stop_loss=95.0, target=110.0, outcome="target_hit", outcome_ts=_dt(2026, 1, 3),
        candles_checked=2,
    )
    kwargs.update(overrides)
    with session_factory() as session:
        session.add(PatternPrediction(**kwargs))
        session.commit()


def test_a_winning_trade_computes_gross_and_net_pnl_correctly(session_factory):
    instrument_id = _register(session_factory)
    _prediction(session_factory, instrument_id, entry_price=100.0, target=110.0, outcome="target_hit")

    trades = compute_trades(session_factory, SYMBOL, SEG, "1day", _dt(2026, 1, 1), _dt(2026, 1, 10), capital_per_trade=10_000.0)

    assert len(trades) == 1
    t = trades[0]
    # quantity = floor(10000 / 100) = 100; gross = 100 * (110 - 100) = 1000
    assert t["quantity"] == 100
    assert t["gross_pnl"] == 1000.0
    # cost = 100 * 100 * 0.00085 = 8.5
    assert t["net_pnl"] == 991.5


def test_a_losing_bear_trade_computes_correctly(session_factory):
    instrument_id = _register(session_factory)
    _prediction(
        session_factory, instrument_id, direction="bear", entry_price=200.0,
        stop_loss=210.0, target=180.0, outcome="stop_hit",
    )

    trades = compute_trades(session_factory, SYMBOL, SEG, "1day", _dt(2026, 1, 1), _dt(2026, 1, 10), capital_per_trade=20_000.0)

    t = trades[0]
    # quantity = floor(20000/200) = 100; bear loss: gross = 100 * -1 * (210-200) = -1000
    assert t["quantity"] == 100
    assert t["gross_pnl"] == -1000.0
    assert t["net_pnl"] == -1017.0  # cost = 100*200*0.00085 = 17


def test_a_sideways_trade_exits_at_the_actual_close_when_the_window_timed_out(session_factory):
    instrument_id = _register(session_factory)
    _prediction(
        session_factory, instrument_id, entry_price=100.0, target=110.0, stop_loss=95.0,
        outcome="sideways", outcome_ts=_dt(2026, 1, 5), candles_checked=25,
    )
    with session_factory() as session:
        session.add(CandleHistorical(
            symbol=SYMBOL, exchange_segment=SEG, timeframe="1day", ts=_dt(2026, 1, 5),
            open_price=101, high_price=104, low_price=100, close_price=103, volume=1000,
        ))
        session.commit()

    trades = compute_trades(session_factory, SYMBOL, SEG, "1day", _dt(2026, 1, 1), _dt(2026, 1, 10), capital_per_trade=10_000.0)

    t = trades[0]
    assert t["exit_price"] == 103.0  # the real close at outcome_ts, not target/stop
    assert t["quantity"] == 100
    assert t["gross_pnl"] == 300.0  # 100 * (103 - 100)


def test_pending_predictions_are_excluded(session_factory):
    instrument_id = _register(session_factory)
    _prediction(session_factory, instrument_id, outcome=None, outcome_ts=None)

    trades = compute_trades(session_factory, SYMBOL, SEG, "1day", _dt(2026, 1, 1), _dt(2026, 1, 10), capital_per_trade=10_000.0)
    assert trades == []


def test_summarize_win_ratio_excludes_sideways():
    trades = [
        {"outcome": "target_hit", "net_pnl": 100.0, "outcome_ts": "2026-01-03T00:00:00+00:00"},
        {"outcome": "target_hit", "net_pnl": 50.0, "outcome_ts": "2026-01-04T00:00:00+00:00"},
        {"outcome": "stop_hit", "net_pnl": -80.0, "outcome_ts": "2026-01-05T00:00:00+00:00"},
        {"outcome": "sideways", "net_pnl": 5.0, "outcome_ts": "2026-01-06T00:00:00+00:00"},
        {"outcome": "target_hit", "gross_pnl": 100.0, "net_pnl": 100.0, "outcome_ts": "2026-02-01T00:00:00+00:00"},
    ]
    for t in trades:
        t.setdefault("gross_pnl", t["net_pnl"])

    summary = summarize(trades)

    assert summary["total_trades"] == 5
    assert summary["wins"] == 3
    assert summary["losses"] == 1
    assert summary["sideways"] == 1
    assert summary["win_ratio"] == 3 / 4  # excludes the 1 sideways trade from the denominator
    assert summary["total_net_pnl"] == 175.0
    assert summary["winning_days"] == 4  # each trade above lands on its own distinct day
    assert summary["losing_days"] == 1
    assert summary["yearly_pnl"] == {"2026": 175.0}
    assert summary["monthly_pnl"]["2026-01"] == 75.0
    assert summary["monthly_pnl"]["2026-02"] == 100.0


def test_summarize_handles_no_trades():
    summary = summarize([])
    assert summary["total_trades"] == 0
    assert summary["win_ratio"] is None
    assert summary["total_net_pnl"] == 0
