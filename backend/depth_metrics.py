from __future__ import annotations

from typing import Iterable, Optional


def calculate_depth_metrics(ltp: float, bid_depth: Iterable, ask_depth: Iterable) -> dict:
    """Calculate proximity and liquidity-concentration metrics from order-book depth."""
    bids = [_level_values(level) for level in bid_depth]
    asks = [_level_values(level) for level in ask_depth]
    bids = [level for level in bids if level is not None]
    asks = [level for level in asks if level is not None]

    best_bid = max((level[0] for level in bids), default=None)
    best_ask = min((level[0] for level in asks), default=None)
    total_bid_quantity = sum(level[1] for level in bids)
    total_ask_quantity = sum(level[1] for level in asks)
    total_quantity = total_bid_quantity + total_ask_quantity

    max_bid = max(bids, key=lambda level: level[1], default=None)
    max_ask = max(asks, key=lambda level: level[1], default=None)

    return {
        "ltp": float(ltp),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "nearest_bid_percentage": _distance_percentage(ltp, best_bid),
        "nearest_ask_percentage": _distance_percentage(best_ask, ltp),
        "spread_percentage": _spread_percentage(best_bid, best_ask),
        "total_bid_quantity": total_bid_quantity,
        "total_ask_quantity": total_ask_quantity,
        "bid_pressure_percentage": _percentage(total_bid_quantity, total_quantity),
        "ask_pressure_percentage": _percentage(total_ask_quantity, total_quantity),
        "maximum_bid_price": max_bid[0] if max_bid else None,
        "maximum_bid_quantity": max_bid[1] if max_bid else 0,
        "maximum_bid_percentage": _percentage(max_bid[1], total_bid_quantity) if max_bid else 0.0,
        "maximum_ask_price": max_ask[0] if max_ask else None,
        "maximum_ask_quantity": max_ask[1] if max_ask else 0,
        "maximum_ask_percentage": _percentage(max_ask[1], total_ask_quantity) if max_ask else 0.0,
    }


def _level_values(level) -> Optional[tuple]:
    if isinstance(level, dict):
        price = level.get("price")
        quantity = level.get("quantity")
    else:
        price = getattr(level, "price", None)
        quantity = getattr(level, "quantity", None)
    if price is None or quantity is None:
        return None
    price, quantity = float(price), int(quantity)
    # Dhan's Full-packet depth block always sends exactly 5 levels per side,
    # zero-padded (price=0.0, quantity=0) whenever fewer than 5 real orders
    # exist at that side -- a thin/illiquid stock, or a quiet moment, commonly
    # has 1-2 real levels and 3-4 padding ones. Treating a padding level as a
    # real ₹0 bid/ask corrupted best_bid/best_ask to 0 and, downstream,
    # nearest_bid/ask_percentage to 100 -- confirmed live 2026-10-05
    # (ASHOKLEY showing Best bid 0 / Nearest bid % 100).
    if price <= 0 or quantity <= 0:
        return None
    return price, quantity


def _percentage(value: float, total: float) -> float:
    return round((value / total) * 100, 2) if total else 0.0


def _distance_percentage(first: Optional[float], second: Optional[float]) -> float:
    if first is None or second is None or not first:
        return 0.0
    return round(abs(first - second) / first * 100, 2)


def _spread_percentage(best_bid: Optional[float], best_ask: Optional[float]) -> float:
    if best_bid is None or best_ask is None:
        return 0.0
    midpoint = (best_bid + best_ask) / 2
    return round((best_ask - best_bid) / midpoint * 100, 2) if midpoint else 0.0