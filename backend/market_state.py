from __future__ import annotations

from datetime import datetime, timezone


def build_tick_payload(
    symbol: str,
    exchange: str,
    segment: str,
    ltp: float,
    previous_close: float,
    currency: str = "INR",
    ts: str | None = None,
):
    """Build the normalized frontend tick payload used by Market Watch.

    The backend computes the market-state fields; the frontend simply renders them.
    """
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    absolute_change = float(ltp) - float(previous_close)
    percentage_change = 0.0
    if previous_close:
        percentage_change = round((absolute_change / float(previous_close)) * 100, 2)

    if absolute_change > 0:
        direction = "up"
    elif absolute_change < 0:
        direction = "down"
    else:
        direction = "flat"

    return {
        "type": "tick",
        "symbol": symbol,
        "exchange": exchange,
        "segment": segment,
        "ltp": float(ltp),
        "previous_close": float(previous_close),
        "currency": currency,
        "absolute_change": round(absolute_change, 2),
        "percentage_change": percentage_change,
        "direction": direction,
        "ts": ts,
    }
