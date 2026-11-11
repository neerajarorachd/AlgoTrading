"""Manual trade rules (rulebook 3b). Pure functions: no DB, no broker.
A trade is a plain dict whose keys match the `trades` columns.

- Prices: entry/exit prices are the raw fill (LTP, limit, stop or target as
  touched). Slippage is NOT folded into the price; it is an expense, added
  to `costs` with the charges, so P&L "before expenses" is price-only and
  "after expenses" = minus charges and slippage.
- Slippage applies to market legs only: market entry, stop, manual exit,
  day-end square-off. A limit entry and the target (limit orders) pay none.
- Ticks: a buy's stop hits at price <= stop, target at price >= target
  (mirror for a sell); the fill is the tick price (a gap fills worse/better).
- Candles (restart catch-up): stop first when one candle touches both.
- Intraday squares off at 15:10 IST; delivery never auto-closes.
Times are naive UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

IST = timedelta(hours=5, minutes=30)
DAY_END_HHMM = 1510            # intraday square-off
ENTRY_OPEN_HHMM = 915
INTRADAY_ENTRY_UNTIL = 1510    # exclusive
DELIVERY_ENTRY_UNTIL = 1530    # exclusive

# Delivery charges (assumptions, 2026-11-11, typical NSE discount-broker
# schedule): STT 0.1% on both legs, stamp duty 0.015% on the buy, DP charge
# Rs 12.5 + GST per sell; brokerage/exchange/SEBI/GST as intraday.
_DELIVERY_STT_PCT = 0.1 / 100
_DELIVERY_STAMP_PCT = 0.015 / 100
_DP_CHARGE = 12.5


def naive_utc(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def ist_hhmm(ts: datetime) -> int:
    t = naive_utc(ts) + IST
    return t.hour * 100 + t.minute


def ist_date(ts: datetime):
    return (naive_utc(ts) + IST).date()


def charges(product: str, quantity: int, sell_price: float, buy_price: float) -> float:
    """Exchange/government/broker charges for a round trip, rupees."""
    from scripts.order_backtest import (_BROKERAGE_FLAT, _BROKERAGE_PCT, _EXCHANGE_PCT, _GST_RATE, _SEBI_PCT,
                                        itemized_round_trip_cost)  # lazy: heavy module
    if product != "delivery":
        return itemized_round_trip_cost(quantity, sell_price, buy_price)
    sell_t, buy_t = quantity * sell_price, quantity * buy_price
    total = sell_t + buy_t
    brokerage = 2 * min(_BROKERAGE_FLAT, _BROKERAGE_PCT * (total / 2))
    exchange, sebi = _EXCHANGE_PCT * total, _SEBI_PCT * total
    stt = _DELIVERY_STT_PCT * total
    stamp = _DELIVERY_STAMP_PCT * buy_t
    gst = _GST_RATE * (brokerage + exchange + sebi + _DP_CHARGE)
    return brokerage + exchange + sebi + stt + stamp + _DP_CHARGE + gst


def entry_window_error(product: str, ts: datetime) -> Optional[str]:
    hhmm = ist_hhmm(ts)
    until = DELIVERY_ENTRY_UNTIL if product == "delivery" else INTRADAY_ENTRY_UNTIL
    if not (ENTRY_OPEN_HHMM <= hhmm < until):
        return f"orders are taken 09:15-{until // 100}:{until % 100:02d} IST for {product}"
    return None


def validate_levels(direction: str, ref_price: float, stop: Optional[float], target: Optional[float]) -> Optional[str]:
    """Stop must be on the losing side of the entry price, target on the winning side."""
    buy = direction == "buy"
    if stop is not None and (stop >= ref_price if buy else stop <= ref_price):
        return f"stop must be {'below' if buy else 'above'} the entry price"
    if target is not None and (target <= ref_price if buy else target >= ref_price):
        return f"target must be {'above' if buy else 'below'} the entry price"
    return None


def fill_entry(trade: dict, *, ts: datetime, price: float) -> dict:
    trade.update(status="open", entry_ts=ts, entry_price=round(price, 4), best_price=price, worst_price=price)
    trade["est_costs"] = estimate_costs(trade)
    return trade


def estimate_costs(trade: dict) -> float:
    """Rough round-trip expenses, worked out ONCE at entry (rulebook 3b): charges
    with both legs at the entry price, plus slippage as if the exit were a
    market order. Live P&L "after expenses" = gross - this, so nothing is
    recomputed per tick; the exact figure comes with the trade's detail."""
    entry, qty = trade["entry_price"], trade["quantity"]
    return round(charges(trade.get("product") or "intraday", qty, entry, entry)
                 + _slippage(trade, entry, "manual"), 2)


def limit_fill_price(trade: dict, price: float) -> Optional[float]:
    """A pending limit order fills when the price reaches the limit (at the
    tick price if it gapped through, which is better for us)."""
    lim = trade["limit_price"]
    if trade["direction"] == "buy":
        return price if price <= lim else None
    return price if price >= lim else None


def _slippage(trade: dict, exit_price: float, reason: str) -> float:
    pct = (trade.get("slippage_pct") or 0.0) / 100.0
    legs = 0.0
    if trade.get("entry_type") == "market":
        legs += trade["entry_price"]
    if reason in ("stop", "manual", "day_end"):
        legs += exit_price
    return legs * trade["quantity"] * pct


def pnl(trade: dict, price: float) -> dict:
    """Running (or final) P&L at `price`: gross (price only), costs and net.
    For an open trade the costs assume a market exit at `price`."""
    qty, entry = trade["quantity"], trade["entry_price"]
    buy = trade["direction"] == "buy"
    gross = (price - entry) * qty if buy else (entry - price) * qty
    sell_p, buy_p = (price, entry) if buy else (entry, price)
    cost = charges(trade.get("product") or "intraday", qty, sell_p, buy_p) + _slippage(trade, price, "manual")
    net = gross - cost
    return {"gross_pnl": round(gross, 2), "costs": round(cost, 2), "net_pnl": round(net, 2),
            "net_pct": round(net / (entry * qty) * 100.0, 4)}


def close_trade(trade: dict, *, ts: datetime, price: float, reason: str) -> dict:
    qty, entry = trade["quantity"], trade["entry_price"]
    buy = trade["direction"] == "buy"
    gross = (price - entry) * qty if buy else (entry - price) * qty
    sell_p, buy_p = (price, entry) if buy else (entry, price)
    cost = charges(trade.get("product") or "intraday", qty, sell_p, buy_p) + _slippage(trade, price, reason)
    net = gross - cost
    trade.update(status="closed", exit_ts=ts, exit_price=round(price, 4), exit_reason=reason,
                 gross_pnl=round(gross, 2), costs=round(cost, 2), net_pnl=round(net, 2),
                 net_pct=round(net / (entry * qty) * 100.0, 4))
    return trade


def _track(trade: dict, fav: float, adv: float) -> None:
    buy = trade["direction"] == "buy"
    best, worst = trade.get("best_price"), trade.get("worst_price")
    trade["best_price"] = fav if best is None else (max(best, fav) if buy else min(best, fav))
    trade["worst_price"] = adv if worst is None else (min(worst, adv) if buy else max(worst, adv))


def is_day_end(trade: dict, ts: datetime) -> bool:
    if trade.get("product") == "delivery" or trade.get("status") != "open":
        return False
    return ist_date(ts) != ist_date(trade["entry_ts"]) or ist_hhmm(ts) >= DAY_END_HHMM


def on_price(trade: dict, *, ts: datetime, price: float) -> Optional[str]:
    """Advance one trade by one tick. Returns what happened:
    'filled' (pending limit filled), 'closed', or None."""
    status = trade.get("status")
    if status == "pending":
        if trade.get("product") != "delivery" and ist_hhmm(ts) >= DAY_END_HHMM:
            trade.update(status="cancelled", exit_ts=ts, exit_reason="day_end")
            return "closed"
        fill = limit_fill_price(trade, price)
        if fill is None:
            return None
        fill_entry(trade, ts=ts, price=fill)
        return "filled"
    if status != "open":
        return None
    if is_day_end(trade, ts):
        close_trade(trade, ts=ts, price=price, reason="day_end")
        return "closed"
    _track(trade, price, price)
    buy = trade["direction"] == "buy"
    stop, target = trade.get("stop_price"), trade.get("target_price")
    if stop is not None and (price <= stop if buy else price >= stop):
        close_trade(trade, ts=ts, price=price, reason="stop")
        return "closed"
    if target is not None and (price >= target if buy else price <= target):
        close_trade(trade, ts=ts, price=price, reason="target")
        return "closed"
    lim = trade.get("exit_limit_price")
    if lim is not None and (price >= lim if buy else price <= lim):
        close_trade(trade, ts=ts, price=price, reason="limit")
        return "closed"
    return None


def exit_limit_reached(trade: dict, price: float) -> bool:
    """True when a limit exit at the trade's exit_limit_price would fill at `price`
    (a buy is closed by selling: price >= limit; a sell by buying: price <= limit)."""
    lim = trade.get("exit_limit_price")
    if lim is None:
        return False
    return price >= lim if trade["direction"] == "buy" else price <= lim


def on_candle(trade: dict, *, ts: datetime, open_: float, high: float, low: float, close: float) -> Optional[str]:
    """Restart catch-up on a missed 1-min candle that started at `ts`."""
    status = trade.get("status")
    buy = trade["direction"] == "buy"
    filled = False
    if status == "pending":
        if trade.get("product") != "delivery" and ist_hhmm(ts) >= DAY_END_HHMM:
            trade.update(status="cancelled", exit_ts=ts, exit_reason="day_end")
            return "closed"
        lim = trade["limit_price"]
        if (low <= lim) if buy else (high >= lim):
            gapped = open_ <= lim if buy else open_ >= lim
            fill_entry(trade, ts=ts, price=open_ if gapped else lim)
            status, filled = "open", True
        else:
            return None
    if status != "open":
        return None
    if is_day_end(trade, ts):
        close_trade(trade, ts=ts, price=open_, reason="day_end")
        return "closed"
    _track(trade, high if buy else low, low if buy else high)
    stop, target = trade.get("stop_price"), trade.get("target_price")
    if stop is not None and (low <= stop if buy else high >= stop):
        gapped = open_ <= stop if buy else open_ >= stop
        close_trade(trade, ts=ts, price=open_ if gapped else stop, reason="stop")
        return "closed"
    if target is not None and (high >= target if buy else low <= target):
        gapped = open_ >= target if buy else open_ <= target
        close_trade(trade, ts=ts, price=open_ if gapped else target, reason="target")
        return "closed"
    lim = trade.get("exit_limit_price")
    if lim is not None and (high >= lim if buy else low <= lim):
        gapped = open_ >= lim if buy else open_ <= lim
        close_trade(trade, ts=ts, price=open_ if gapped else lim, reason="limit")
        return "closed"
    return "filled" if filled else None


def exit_legs(trade: dict) -> int:
    """How many exit orders the trade has / will have: stop, target, limit exit."""
    return sum(trade.get(k) is not None for k in ("stop_price", "target_price", "exit_limit_price"))


def capital_used(trade: dict) -> float:
    """Capital a trade holds: full value, no leverage (rulebook 3b). Each exit
    order beyond the first blocks the value again, as a broker does for
    separate exit orders (user, 11-11): stop + target legs = 2x."""
    price = trade.get("entry_price") or trade.get("limit_price") or trade.get("ltp_at_order") or 0.0
    return price * trade["quantity"] * (1 + max(0, exit_legs(trade) - 1))
