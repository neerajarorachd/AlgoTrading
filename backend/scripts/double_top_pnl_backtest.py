"""One-off analysis: applies real Indian equity intraday trading costs
(brokerage, STT, exchange charges, SEBI fee, stamp duty, GST) to every
historical double_top signal at the 0.25/0.50 SL/TG combo, sized to a
stated capital, to see the actual NET outcome rather than raw price
expectancy. A double_top is bearish, so each trade is modeled as an
intraday SHORT: sell at entry, buy to cover at exit.

Cost assumptions (typical discount-broker intraday equity rates — flag and
adjust here if your actual broker/plan differs):
  brokerage:  min(Rs 20, 0.03% of turnover) PER LEG (buy leg + sell leg)
  STT:        0.025% of SELL-side turnover only (intraday equity)
  exchange:   0.00297% of turnover, both legs (NSE)
  SEBI fee:   0.0001% of turnover, both legs
  stamp duty: 0.003% of BUY-side turnover only
  GST:        18% on (brokerage + exchange charge + SEBI fee)
No leverage assumed — capital is deployed 1x, not on margin. Reuses the
exact same detection + SL/TG simulation as double_top_sltg_backtest.py so
results are directly comparable to that script's win rate/expectancy.

Run manually:
    .venv/Scripts/python.exe backend/scripts/double_top_pnl_backtest.py [SYMBOL] [CAPITAL]
"""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import SwingPoint, _SWING_LOOKBACK, detect_double_top, detect_swing_high, detect_swing_low
from brokers.models import Candle
from db.models import SubscribedSymbol
from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory

TIMEFRAME = "1min"
MAX_CANDLES = 120
SL_PCT, TG_PCT = 0.25, 0.50

# A candle's own `volume` is BOTH sides of the tape (every share traded has a
# buyer and a seller counted once each), so real one-side liquidity is
# volume/2 — then a further /20 safety margin on top (explicit instruction:
# "still it is too high but lets assume") since actually clearing even half
# a single 1-minute candle's volume in one order would move the price far
# more than this backtest's zero-slippage fills assume. Net: volume/40.
LIQUIDITY_SAFETY_DIVISOR = 40

BROKERAGE_FLAT = 20.0
BROKERAGE_PCT = 0.03 / 100
STT_PCT = 0.025 / 100
EXCHANGE_PCT = 0.00297 / 100
SEBI_PCT = 0.0001 / 100
STAMP_DUTY_PCT = 0.003 / 100
GST_RATE = 0.18


def find_detections(candles: list[Candle]) -> list[dict]:
    window = deque(maxlen=2 * _SWING_LOOKBACK + 1)
    points: list[SwingPoint] = []
    point_index: list[int] = []
    detections = []
    for i, candle in enumerate(candles):
        window.append(candle)
        if len(window) < window.maxlen:
            continue
        window_list = list(window)
        swing_candle_index = i - _SWING_LOOKBACK
        for kind_check, kind_name in ((detect_swing_high, "high"), (detect_swing_low, "low")):
            if not kind_check(window_list, _SWING_LOOKBACK):
                continue
            swing_candle = window_list[_SWING_LOOKBACK]
            price = swing_candle.high if kind_name == "high" else swing_candle.low
            points.append(SwingPoint(kind=kind_name, price=price, candle=swing_candle))
            point_index.append(swing_candle_index)
            if kind_name == "high" and detect_double_top(points):
                c_idx = point_index[-1]
                detections.append({"entry_index": c_idx, "entry_price": candles[c_idx].close})
    return detections


def simulate_exit(candles: list[Candle], entry_index: int, entry_price: float) -> tuple[str, float, int]:
    """Returns (result, exit_price, exit_index) for the SHORT (stop above,
    target below) — exit_index is the candle at which capital frees up
    again, needed by the caller to enforce one-position-at-a-time."""
    stop_price = entry_price * (1 + SL_PCT / 100)
    target_price = entry_price * (1 - TG_PCT / 100)
    entry_day = candles[entry_index].timestamp.date()
    last_seen_index = entry_index
    for j in range(entry_index + 1, min(entry_index + 1 + MAX_CANDLES, len(candles))):
        c = candles[j]
        if c.timestamp.date() != entry_day:
            break
        last_seen_index = j
        if c.high >= stop_price:
            return "loss", stop_price, j
        if c.low <= target_price:
            return "win", target_price, j
    return "timeout", candles[last_seen_index].close, last_seen_index


def trade_costs(qty: int, sell_price: float, buy_price: float) -> float:
    """sell_price = entry (short sale leg), buy_price = exit (cover leg)."""
    sell_turnover = qty * sell_price
    buy_turnover = qty * buy_price
    total_turnover = sell_turnover + buy_turnover

    brokerage = 2 * min(BROKERAGE_FLAT, BROKERAGE_PCT * (total_turnover / 2))
    stt = STT_PCT * sell_turnover
    exchange_charge = EXCHANGE_PCT * total_turnover
    sebi_fee = SEBI_PCT * total_turnover
    stamp_duty = STAMP_DUTY_PCT * buy_turnover
    gst = GST_RATE * (brokerage + exchange_charge + sebi_fee)
    return brokerage + stt + exchange_charge + sebi_fee + stamp_duty + gst


def main() -> None:
    symbol_arg = sys.argv[1] if len(sys.argv) > 1 else "HINDCOPPER"
    capital = float(sys.argv[2]) if len(sys.argv) > 2 else 200_000.0

    engine = build_engine()
    session_factory = build_session_factory(engine)
    with session_factory() as session:
        symbol_row = session.query(SubscribedSymbol).filter_by(symbol=symbol_arg).one()
        historical = LibCandlesHistorical.get_range(session, symbol_arg, symbol_row.exchange_segment, TIMEFRAME)

    candles = [
        Candle(symbol=symbol_arg, timeframe=TIMEFRAME, timestamp=r.ts,
               open=float(r.open_price), high=float(r.high_price),
               low=float(r.low_price), close=float(r.close_price), volume=r.volume)
        for r in historical
    ]
    detections = find_detections(candles)
    compound = "--compound" in sys.argv
    print(f"{symbol_arg}: {len(detections)} double_top signals detected | SL={SL_PCT}% TG={TG_PCT}% | "
          f"starting capital=Rs {capital:,.0f} | mode={'compounding' if compound else 'fixed'}")
    print("(one position at a time — a signal firing while a trade is still open is skipped, not queued)\n")

    wins = losses = timeouts = skipped_overlap = 0
    gross_total = cost_total = net_total = 0.0
    worst_trade = None
    best_trade = None
    next_free_index = -1  # candle index at which capital is next available

    running_capital = capital  # only actually moves in --compound mode
    peak_capital = capital
    max_drawdown_pct = 0.0
    max_drawdown_rs = 0.0
    liquidity_capped_count = 0

    for d in detections:
        entry_index = d["entry_index"]
        if entry_index <= next_free_index:
            skipped_overlap += 1
            continue

        entry_price = d["entry_price"]
        result, exit_price, exit_index = simulate_exit(candles, entry_index, entry_price)
        size_base = running_capital if compound else capital
        capital_qty = int(size_base // entry_price)
        liquidity_qty = candles[entry_index].volume // LIQUIDITY_SAFETY_DIVISOR
        qty = min(capital_qty, liquidity_qty)
        if qty < capital_qty:
            liquidity_capped_count += 1
        if qty <= 0:
            continue
        next_free_index = exit_index

        gross = qty * (entry_price - exit_price)  # short: profit when exit < entry
        costs = trade_costs(qty, sell_price=entry_price, buy_price=exit_price)
        net = gross - costs

        gross_total += gross
        cost_total += costs
        net_total += net
        if compound:
            running_capital += net
            peak_capital = max(peak_capital, running_capital)
            drawdown_rs = peak_capital - running_capital
            drawdown_pct = drawdown_rs / peak_capital * 100 if peak_capital else 0.0
            max_drawdown_rs = max(max_drawdown_rs, drawdown_rs)
            max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)
        if result == "win":
            wins += 1
        elif result == "loss":
            losses += 1
        else:
            timeouts += 1

        if worst_trade is None or net < worst_trade:
            worst_trade = net
        if best_trade is None or net > best_trade:
            best_trade = net

    n = wins + losses + timeouts
    decided = wins + losses
    print(f"Signals skipped (capital already deployed): {skipped_overlap}")
    print(f"Trades actually taken: {n} (win {wins}, loss {losses}, timeout {timeouts})")
    print(f"Trades where liquidity capped size below what capital allowed: {liquidity_capped_count}")
    print(f"Decided-only win rate: {wins/decided*100:.1f}%\n")
    print(f"Gross P&L (price only):      Rs {gross_total:>14,.0f}")
    print(f"Total costs (brokerage+taxes): Rs {cost_total:>12,.0f}  ({cost_total/abs(gross_total)*100:.1f}% of gross)"
          if gross_total else f"Total costs: Rs {cost_total:,.0f}")
    print(f"NET P&L after costs:          Rs {net_total:>14,.0f}")
    print(f"Net P&L per trade (avg):      Rs {net_total/n:>14,.2f}")
    print(f"Avg cost per trade:           Rs {cost_total/n:>14,.2f}")
    print(f"Best single trade:            Rs {best_trade:>14,.2f}")
    print(f"Worst single trade:           Rs {worst_trade:>14,.2f}")

    if compound:
        total_return_pct = (running_capital - capital) / capital * 100
        print(f"\nStarting capital:             Rs {capital:>14,.0f}")
        print(f"Final capital:                Rs {running_capital:>14,.0f}")
        print(f"Total return:                 {total_return_pct:>14.1f}%")
        print(f"Max drawdown (equity curve):  Rs {max_drawdown_rs:>14,.0f}  ({max_drawdown_pct:.1f}% of peak)")
    else:
        print(f"Net return on capital per trade: {net_total/n/capital*100:.4f}%")


if __name__ == "__main__":
    main()
