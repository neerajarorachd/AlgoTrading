"""Day-by-day diagnostic trace for order_backtest.py's OrderBook — prints
every candle for one trading day, then every trade's full lifecycle
(created + closed events with the triggering candle's OHLC), so the
mechanics can be verified by hand rather than trusted from aggregate P&L
alone. Built on explicit request ("let's test what's going on actually...
one day test at a time") after order_backtest.py's aggregate HINDCOPPER
3-min results looked surprisingly bad.

Reuses order_backtest.py's OrderBook/EngineConfig/SLTargetConfig/
_sl_target_for entirely — this is a presentation layer on the same
mechanics, not a second implementation.

Run manually, ONE trading day at a time:

    .venv/Scripts/python.exe backend/scripts/order_backtest_trace.py \\
        SYMBOL TIMEFRAME DATE SL_PCT TARGET_PCT

e.g.
    .venv/Scripts/python.exe backend/scripts/order_backtest_trace.py \\
        HINDCOPPER 3min 2025-03-03 0.004 0.008
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine, seed_pattern_definitions
from db.models import Base
from db.ops import LibSymbols
from db.session import build_engine, build_session_factory
from order_backtest import (
    BEARISH_PATTERNS, BULLISH_PATTERNS, EngineConfig, OrderBook, PREDICTION_SETTING_DEFAULTS,
    SLTargetConfig, _load_candles, _sl_target_for, ist_date,
)
from prediction_tracker import load_prediction_settings

IST_OFFSET = timedelta(hours=5, minutes=30)


def _ist(ts_utc: datetime) -> datetime:
    return ts_utc + IST_OFFSET


def _fmt_candle(c) -> str:
    return (f"{_ist(c.timestamp).strftime('%H:%M')}  "
            f"O={c.open:.2f} H={c.high:.2f} L={c.low:.2f} C={c.close:.2f} V={c.volume}")


def main() -> None:
    symbol, timeframe, date_s, sl_pct_s, target_pct_s = sys.argv[1:6]
    day = datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    sl_pct, target_pct = float(sl_pct_s), float(target_pct_s)

    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    Base.metadata.create_all(engine_db)
    seed_pattern_definitions(session_factory)

    with session_factory() as session:
        row = LibSymbols.get_by_natural_key(session, symbol, "NSE", "EQUITY")
        exchange_segment = row.exchange_segment

    # A generous window either side of the target day: candles from the
    # day before are still needed to warm up the ActivityEngine's own
    # indicator state (RSI/MACD/ATR/etc. need real lookback, not a cold
    # start at 09:15) — only the target day's own candles get PRINTED and
    # only the target day's own trades get counted, but the engine sees a
    # realistic amount of prior history first, same as any other replay.
    warmup_start = day - timedelta(days=30)
    window_end = day + timedelta(days=1)
    all_candles = _load_candles(session_factory, symbol, exchange_segment, timeframe, warmup_start, window_end)
    day_candles = [c for c in all_candles if ist_date(c.timestamp) == day.date()]
    print(f"{symbol} {timeframe} — {date_s}: {len(day_candles)} candles this day "
          f"({len(all_candles)} total incl. {len(all_candles) - len(day_candles)} warm-up candles before it)\n")

    if not day_candles:
        print("No candles for this day (holiday/weekend, or not yet fetched).")
        return

    print("=" * 78)
    print(f"FULL DAY CANDLES — {date_s} ({timeframe}, IST)")
    print("=" * 78)
    for c in day_candles:
        print(f"  {_fmt_candle(c)}")

    config = EngineConfig(sl_target=SLTargetConfig(f"SL {sl_pct*100:.1f}% / Target {target_pct*100:.1f}%", "fixed_pct", sl_pct=sl_pct, target_pct=target_pct))
    engine = ActivityEngine(session_factory)
    settings = load_prediction_settings(session_factory)
    atr_multiplier = settings.get("crossover_atr_stop_multiplier", PREDICTION_SETTING_DEFAULTS["crossover_atr_stop_multiplier"])
    risk_reward_ratio = settings.get("crossover_risk_reward_ratio", PREDICTION_SETTING_DEFAULTS["crossover_risk_reward_ratio"])
    book = OrderBook(config)

    # opened[i] mirrors book.closed_trades[i] once that trade closes — kept
    # separately only for the (rare, cross-boundary) case a trade is still
    # open when this window ends, so it can still be reported as "created,
    # never closed in this window" rather than silently dropped.
    opened_this_day: list = []

    for candle in all_candles:
        book.on_new_candle_day(candle.timestamp)
        pre_close_count = len(book.closed_trades)
        book.resolve_against_candle(candle)
        book.maybe_squareoff(candle)

        new_activities = engine.on_candle_closed(symbol, exchange_segment, candle)
        for activity in new_activities:
            pattern = activity["activity"]
            if pattern in BULLISH_PATTERNS:
                direction = "bull"
            elif pattern in BEARISH_PATTERNS:
                direction = "bear"
            else:
                continue
            if not book.can_open(candle.timestamp):
                continue
            entry = float(candle.close)  # see order_backtest.py's own comment on this exact line
            levels = _sl_target_for(
                engine, symbol, exchange_segment, candle.timeframe, pattern, direction, entry,
                config.sl_target, atr_multiplier, risk_reward_ratio,
            )
            if levels is None:
                continue
            stop_loss, target = levels
            before = len(book.open_positions)
            opened = book.try_open(pattern, direction, entry, candle.timestamp, stop_loss, target, candle.timeframe)
            if opened and ist_date(candle.timestamp) == day.date():
                opened_this_day.append({
                    "pattern": pattern, "direction": direction, "entry_ts": candle.timestamp,
                    "entry_price": entry, "stop_loss": stop_loss, "target": target, "entry_candle": candle,
                })

    print("\n" + "=" * 78)
    print(f"TRADES — {date_s}")
    print("=" * 78)
    day_trades = [t for t in book.closed_trades if ist_date(datetime.fromisoformat(t["entry_ts"])) == day.date()]
    if not day_trades:
        print("  No trades opened this day.")
    for i, t in enumerate(day_trades, 1):
        entry_ts = datetime.fromisoformat(t["entry_ts"])
        exit_ts = datetime.fromisoformat(t["exit_ts"])
        entry_candle = next((c for c in all_candles if c.timestamp == entry_ts and c.timeframe == timeframe), None)
        exit_candle = next((c for c in all_candles if c.timestamp == exit_ts and c.timeframe == timeframe), None)

        print(f"\n  Trade #{i}: {t['pattern']} ({t['direction']})")
        print(f"    CREATED  {_ist(entry_ts).strftime('%H:%M')}  "
              f"candle[{_fmt_candle(entry_candle) if entry_candle else 'n/a'}]")
        print(f"             direction={t['direction']}  entry={t['entry_price']:.2f}  "
              f"SL={t['stop_loss']:.2f}  target={t['target']:.2f}  qty={t['quantity']}")
        print(f"    CLOSED   {_ist(exit_ts).strftime('%H:%M')}  "
              f"candle[{_fmt_candle(exit_candle) if exit_candle else 'n/a'}]")
        print(f"             reason={t['exit_reason']}  exit_price={t['exit_price']:.2f}  "
              f"gross={t['gross_pnl']:.2f}  expenses={t['expenses']:.2f}  net={t['net_pnl']:.2f}")

    print(f"\nDay summary: {len(day_trades)} trade(s), "
          f"net = {sum(t['net_pnl'] for t in day_trades):.2f}")


if __name__ == "__main__":
    main()
