"""Paper trade rules, shared by live paper trading and paper backtests
(rulebook 3a), so both behave exactly the same. Pure functions: no DB, no
broker. Trades are plain dicts whose keys match the paper trade table
columns (db/models_paper.py), so callers copy them straight onto a row.

- Open: at the hit candle's close (the current candle, never a swing
  candle's) plus slippage against us. Stop / target = entry -/+ N x ATR.
- Every hit gets a row: hits that can't be taken are "not_taken" + reason.
- Exits, checked on each later candle (1-min live): stop first when a candle
  touches both (safe side); a candle opening past the stop/target fills at
  its open; square-off at day end. Market exits (stop, day end) pay
  slippage, the target (a limit order) does not.
- Costs: itemized_round_trip_cost, the same model as the research.
Times are UTC (naive = UTC); entry window / day end are IST HHMM.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

IST = timedelta(hours=5, minutes=30)


@dataclass
class PaperSettings:
    stop_atr: float = 2.0
    target_atr: float = 10.0
    amount_per_trade: float = 100000.0
    entry_from: int = 930     # HHMM IST
    entry_until: int = 1445   # HHMM IST, exclusive
    day_end: int = 1510       # HHMM IST
    slippage_pct: float = 0.05

    @classmethod
    def from_row(cls, row) -> "PaperSettings":
        return cls(**{f: getattr(row, f) for f in cls.__dataclass_fields__})


def ist_hhmm(ts: datetime) -> int:
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    t = ts + IST
    return t.hour * 100 + t.minute


def ist_date(ts: datetime):
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return (ts + IST).date()


def round_trip_cost(quantity: int, sell_price: float, buy_price: float) -> float:
    from scripts.order_backtest import itemized_round_trip_cost  # lazy: heavy module
    return itemized_round_trip_cost(quantity, sell_price, buy_price)


def open_trade(*, hit_ts: datetime, direction: str, hit_close: float, atr: Optional[float],
               settings: PaperSettings, already_open: bool = False) -> dict:
    """Decide what happens to one hit. direction: 'buy' | 'sell'.
    Returns the trade fields: status 'open' (with entry/qty/stop/target)
    or 'not_taken' (with not_taken_reason)."""
    trade = {"hit_ts": hit_ts, "direction": direction, "hit_close": hit_close, "atr": atr}

    def skip(reason):
        trade.update(status="not_taken", not_taken_reason=reason)
        return trade

    hhmm = ist_hhmm(hit_ts)
    if direction not in ("buy", "sell"):
        return skip("no direction")
    if not (settings.entry_from <= hhmm < settings.entry_until):
        return skip(f"outside entry window {settings.entry_from}-{settings.entry_until}")
    if already_open:
        return skip("already in a trade on this stock for this item")
    if atr is None or atr <= 0:
        return skip("no ATR yet")
    slip = settings.slippage_pct / 100.0
    entry = hit_close * (1 + slip) if direction == "buy" else hit_close * (1 - slip)
    quantity = int(settings.amount_per_trade // entry)
    if quantity < 1:
        return skip("price above amount per trade")
    sign = 1 if direction == "buy" else -1
    trade.update(
        status="open", entry_ts=hit_ts, entry_price=round(entry, 4), quantity=quantity,
        stop_price=round(entry - sign * settings.stop_atr * atr, 4),
        target_price=round(entry + sign * settings.target_atr * atr, 4),
        best_pct=0.0, worst_pct=0.0,
    )
    return trade


def _move_pct(trade: dict, price: float) -> float:
    sign = 1 if trade["direction"] == "buy" else -1
    return sign * (price - trade["entry_price"]) / trade["entry_price"] * 100.0


def close_trade(trade: dict, *, exit_ts: datetime, exit_price: float, reason: str,
                slippage_pct: float = 0.0) -> dict:
    """Fill the exit and P&L fields. Slippage (market exits) goes against us."""
    slip = slippage_pct / 100.0
    if trade["direction"] == "buy":
        price = exit_price * (1 - slip)
        gross = (price - trade["entry_price"]) * trade["quantity"]
        costs = round_trip_cost(trade["quantity"], price, trade["entry_price"])
    else:
        price = exit_price * (1 + slip)
        gross = (trade["entry_price"] - price) * trade["quantity"]
        costs = round_trip_cost(trade["quantity"], trade["entry_price"], price)
    net = gross - costs
    trade.update(
        status="closed", exit_ts=exit_ts, exit_price=round(price, 4), exit_reason=reason,
        gross_pnl=round(gross, 2), costs=round(costs, 2), net_pnl=round(net, 2),
        net_pct=round(net / (trade["entry_price"] * trade["quantity"]) * 100.0, 4),
    )
    return trade


def on_candle(trade: dict, *, ts: datetime, open_: float, high: float, low: float, close: float,
              settings: PaperSettings) -> bool:
    """Advance one open trade by one candle that started at `ts` (after the
    entry). Returns True when the trade closed on this candle."""
    if trade.get("status") != "open":
        return False
    buy = trade["direction"] == "buy"
    stop, target = trade["stop_price"], trade["target_price"]
    # a new day, or the day-end candle: square off at its open
    if ist_date(ts) != ist_date(trade["entry_ts"]) or ist_hhmm(ts) >= settings.day_end:
        close_trade(trade, exit_ts=ts, exit_price=open_, reason="day_end", slippage_pct=settings.slippage_pct)
        return True
    fav, adv = (high, low) if buy else (low, high)
    trade["best_pct"] = round(max(trade.get("best_pct") or 0.0, _move_pct(trade, fav)), 4)
    trade["worst_pct"] = round(min(trade.get("worst_pct") or 0.0, _move_pct(trade, adv)), 4)
    stop_hit = low <= stop if buy else high >= stop
    target_hit = high >= target if buy else low <= target
    if stop_hit:  # checked first: if both touch in one candle, assume the stop
        gapped = open_ <= stop if buy else open_ >= stop
        close_trade(trade, exit_ts=ts, exit_price=open_ if gapped else stop, reason="stop",
                    slippage_pct=settings.slippage_pct)
        return True
    if target_hit:
        gapped = open_ >= target if buy else open_ <= target
        close_trade(trade, exit_ts=ts, exit_price=open_ if gapped else target, reason="target")
        return True
    return False
