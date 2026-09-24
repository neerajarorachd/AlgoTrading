"""Real-money version of verify_double_top_depth_filter.py's 0.8% finding:
does pre-filtering double_top signals by (entry_price - neckline_low) /
entry_price >= 0.8% actually improve the real backtest (real Rs 200k
capital, real itemized costs, real liquidity cap, spread_fills, the
already-proven discipline stack: widened stop, 1% gross daily loss cap,
exit at first loss)?

Mirrors order_backtest.simulate()'s own loop closely (same OrderBook/
ActivityEngine wiring) but adds one extra gate before try_open() for
double_top specifically: only take the signal if this candle's own real
entry_index was pre-computed as "deep enough" by the same swing-point
replay verify_double_top_depth_filter.py uses.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_double_top_depth_filter_money.py
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine, SwingPoint, _SWING_LOOKBACK, detect_double_top, detect_swing_high, detect_swing_low
from brokers.models import Candle
from db.models import SubscribedSymbol
from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory
from order_backtest import EngineConfig, OrderBook, SLTargetConfig, fixed_pct_levels, summarize

SYMBOL = "HINDCOPPER"
EXCHANGE_SEGMENT = "NSE_EQ"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
STARTING_CAPITAL = 200_000.0
DEPTH_THRESHOLDS = [0.007, 0.008, 0.010, 0.012]  # explicit request: check .7/.8/1.0/1.2%


def load_candles(session_factory):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    with session_factory() as session:
        row = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one()
        exchange_segment = row.exchange_segment
        historical = LibCandlesHistorical.get_range(session, SYMBOL, exchange_segment, TIMEFRAME, start, end)
    candles = [
        Candle(symbol=SYMBOL, timeframe=TIMEFRAME, timestamp=(c.ts if c.ts.tzinfo else c.ts.replace(tzinfo=timezone.utc)),
               open=float(c.open_price), high=float(c.high_price), low=float(c.low_price),
               close=float(c.close_price), volume=c.volume)
        for c in historical
    ]
    return candles, exchange_segment


def depth_pct_by_index(candles) -> dict[int, float]:
    """Same swing-point replay as verify_double_top_depth_filter.py --
    returns every double_top detection's depth_pct (REAL entry price vs.
    the neckline low), keyed by candle index, so multiple thresholds can
    be swept without re-replaying the swing points each time."""
    window = deque(maxlen=2 * _SWING_LOOKBACK + 1)
    points: list[SwingPoint] = []
    depths: dict[int, float] = {}
    for i, candle in enumerate(candles):
        window.append(candle)
        if len(window) < window.maxlen:
            continue
        window_list = list(window)
        for kind_check, kind_name in ((detect_swing_high, "high"), (detect_swing_low, "low")):
            if not kind_check(window_list, _SWING_LOOKBACK):
                continue
            swing_candle = window_list[_SWING_LOOKBACK]
            price = swing_candle.high if kind_name == "high" else swing_candle.low
            points.append(SwingPoint(kind=kind_name, price=price, candle=swing_candle))
            if kind_name == "high" and detect_double_top(points):
                b = points[-2]
                entry_price = float(candle.close)
                depths[i] = (entry_price - b.price) / entry_price if entry_price else 0.0
    return depths


def run(session_factory, candles, exchange_segment, allowed_indices: set[int] | None) -> dict:
    config = EngineConfig(
        capital_per_trade=STARTING_CAPITAL,
        max_vol_per_call=100_000,
        max_orders_at_a_time=1,
        exit_at_loss_count=1,
        liquidity_safety_divisor=40,
        spread_fills=True, max_fill_candles=3, max_exit_candles=10,
        itemized_costs=True,
        compounding=True,
        pattern_filter=("double_top",),
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=0.004, target_pct=0.008),
        max_daily_loss_pct=0.01,
    )
    engine = ActivityEngine(session_factory)
    book = OrderBook(config)
    for i, candle in enumerate(candles):
        ts = candle.timestamp
        book.on_new_candle_day(ts)
        book.resolve_against_candle(candle)
        book.continue_closing(candle)
        book.continue_entries(candle)
        book.maybe_squareoff(candle)

        for activity in engine.on_candle_closed(SYMBOL, exchange_segment, candle):
            if activity["activity"] != "double_top":
                continue
            if allowed_indices is not None and i not in allowed_indices:
                continue
            if not book.can_open(ts):
                continue
            entry = float(candle.close)
            stop_loss, target = fixed_pct_levels(entry, "bear", 0.004, 0.008)
            book.try_open(SYMBOL, "double_top", "bear", entry, ts, stop_loss, target, candle.timeframe, candle.volume)

    if candles:
        book.force_close_all(float(candles[-1].close), candles[-1].timestamp)

    stats = summarize(book.closed_trades)
    stats["final_capital"] = STARTING_CAPITAL + stats["total_net_pnl"]
    return stats


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    candles, exchange_segment = load_candles(session_factory)
    print(f"{SYMBOL}: {len(candles)} candles loaded\n", flush=True)

    depths = depth_pct_by_index(candles)
    print(f"{len(depths)} total double_top detections\n", flush=True)

    runs = [("NO depth filter (baseline)", None)]
    for threshold in DEPTH_THRESHOLDS:
        allowed = {i for i, d in depths.items() if d >= threshold}
        runs.append((f"WITH >= {threshold*100:.1f}% depth filter (N={len(allowed)} signals)", allowed))

    for label, allowed_set in runs:
        stats = run(session_factory, candles, exchange_segment, allowed_set)
        wr = f"{stats['win_ratio']*100:.1f}%" if stats["win_ratio"] is not None else "n/a"
        ret = (stats["final_capital"] / STARTING_CAPITAL - 1) * 100
        print(f"=== {label} ===")
        print(f"Trades: {stats['total_trades']}  Win: {stats['wins']}  Loss: {stats['losses']}  Win ratio: {wr}")
        print(f"Gross P&L: Rs {stats['total_gross_pnl']:,.2f}  Expenses: Rs {stats['total_expenses']:,.2f}")
        print(f"Net P&L: Rs {stats['total_net_pnl']:,.2f}  Final capital: Rs {stats['final_capital']:,.2f}  "
              f"Return: {ret:.1f}%\n", flush=True)


if __name__ == "__main__":
    main()
