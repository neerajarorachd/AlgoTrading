"""Dhan market-feed packet decoding used by the broker transport."""
import json
import struct
from datetime import datetime, timezone


_EXCHANGE_SEGMENTS = {
    1: "NSE_EQ",
    2: "NSE_FNO",
    3: "NSE_CURRENCY",
    4: "BSE_EQ",
    5: "BSE_FNO",
    7: "BSE_CURRENCY",
    8: "MCX_COMM",
    9: "NCDEX_COMM",
}


def decode_market_data(message):
    """Decode Dhan JSON test messages and binary ticker/quote packets."""
    if isinstance(message, str):
        return json.loads(message)
    if isinstance(message, bytearray):
        message = bytes(message)
    if not isinstance(message, bytes) or not message:
        raise ValueError("Dhan feed message must be a non-empty JSON string or bytes")

    if len(message) >= 12 and message[2] in (41, 51):
        return decode_full_market_depth(message)

    packet_type = message[0]
    if packet_type == 2:
        return _decode_ticker(message)
    if packet_type == 4:
        return _decode_quote(message)
    if packet_type == 6:
        return _decode_previous_close(message)
    if packet_type == 8:
        return _decode_full(message)
    raise ValueError(f"Unsupported Dhan market-feed packet type: {packet_type}")


def decode_full_market_depth(message):
    """Decode one Dhan 20- or 200-level depth packet."""
    if len(message) < 12:
        raise ValueError("Dhan full-depth packet is shorter than its header")

    message_length, response_code, exchange, security_id, row_count = struct.unpack(
        "<HBBII", message[:12]
    )
    level_count = 200 if row_count == 200 else 20
    expected_length = 12 + level_count * 16
    if len(message) < expected_length:
        raise ValueError("Dhan full-depth packet is incomplete")

    levels = []
    for offset in range(12, expected_length, 16):
        price, quantity, orders = struct.unpack("<dII", message[offset:offset + 16])
        levels.append({"price": price, "quantity": quantity, "orders": orders})

    return {
        "type": "Full Market Depth",
        "exchange_segment": _EXCHANGE_SEGMENTS.get(exchange, exchange),
        "security_id": str(security_id),
        "side": "buy" if response_code == 41 else "sell",
        "levels": levels,
        "level_count": level_count,
        "message_length": message_length,
    }


def decode_full_market_depth_packets(message):
    """Decode all concatenated full-depth packets in one WebSocket message."""
    packets = []
    offset = 0
    while offset < len(message):
        if len(message) - offset < 12:
            raise ValueError("Dhan full-depth message has an incomplete packet header")
        packet_length = struct.unpack("<H", message[offset:offset + 2])[0]
        if packet_length < 12 or offset + packet_length > len(message):
            raise ValueError("Dhan full-depth message has an invalid packet length")
        packets.append(decode_full_market_depth(message[offset:offset + packet_length]))
        offset += packet_length
    return packets


def _decode_ticker(message):
    packet_type, _, exchange, security_id, ltp, epoch = struct.unpack("<BHBIfI", message[:16])
    return _base_tick(packet_type, exchange, security_id, ltp, epoch)


def _decode_quote(message):
    values = struct.unpack("<BHBIfHIfIIIffff", message[:50])
    return _base_tick(values[0], values[2], values[3], values[4], values[6], {
        "volume": values[8], "open": values[11], "close": values[12],
        "high": values[13], "low": values[14],
    })


def _decode_previous_close(message):
    _, _, exchange, security_id, previous_close, _ = struct.unpack("<BHBIfI", message[:16])
    return {
        "type": "Previous Close",
        "exchange_segment": _EXCHANGE_SEGMENTS.get(exchange, exchange),
        "security_id": str(security_id),
        "prev_close": float(previous_close),
    }


def _decode_full(message):
    values = struct.unpack("<BHBIfHIfIIIIIIffff100s", message[:162])
    buy_depth, sell_depth = _decode_full_packet_depth(values[18])
    return _base_tick(values[0], values[2], values[3], values[4], values[6], {
        "volume": values[8], "open": values[14], "close": values[15],
        "high": values[16], "low": values[17],
        "buy_depth": buy_depth, "sell_depth": sell_depth,
    })


def _decode_full_packet_depth(raw_levels):
    """Decode the Full packet's trailing 100-byte, 5-level depth block: each
    20-byte level is {bid_qty, ask_qty, bid_orders, ask_orders, bid_price,
    ask_price}, best level first."""
    buy_depth, sell_depth = [], []
    for offset in range(0, len(raw_levels), 20):
        bid_qty, ask_qty, bid_orders, ask_orders, bid_price, ask_price = struct.unpack(
            "<IIHHff", raw_levels[offset:offset + 20]
        )
        buy_depth.append({"price": bid_price, "quantity": bid_qty, "orders": bid_orders})
        sell_depth.append({"price": ask_price, "quantity": ask_qty, "orders": ask_orders})
    return buy_depth, sell_depth


_DHAN_WS_EPOCH_IST_BIAS_SECONDS = 19800  # 5:30 — see _base_tick's own docstring


def _base_tick(packet_type, exchange, security_id, ltp, epoch, fields=None):
    """Real bug found and fixed 2026-09-17: Dhan's LIVE WebSocket feed's own
    `epoch` field is NOT a true Unix/UTC epoch — cross-checked against
    DhanBroker.get_historical_data's `timestamp` array (the REST /charts/
    intraday endpoint), which correctly reproduces real UTC "now" to the
    second, this WS `epoch` field consistently reads exactly +19800s (the
    IST offset) ahead of the true instant, i.e. it's really "the correct
    instant, computed as if IST were UTC." Confirmed live: real UTC now was
    04:53:37 while `candles_today`'s freshest live-tick-driven row (the ONLY
    consumer of this function) was already at 10:22 — an unbroken, gapless
    sequence all day (no jump where backfill's correct-UTC rows would meet
    live-tick rows), so nothing but this decode has been writing today's
    candle timestamps. Left unfixed, this doesn't just mislabel the chart —
    LibCandles.get_last_ts (feed/gap_fill.py's watermark) reads this same
    inflated "future" timestamp and considers every symbol permanently
    caught up, silently disabling the whole gap-fill safety net for any
    symbol with at least one live tick. candles_historical is unaffected
    (loaded via the same correct REST endpoint verified above, not this
    live path). Subtracting the bias before conversion recovers the true
    instant; only this one call site changes — get_historical_data's own
    decode was independently verified correct and is untouched."""
    tick = {
        "type": {2: "Ticker Data", 4: "Quote Data", 8: "Full Data"}.get(packet_type),
        "exchange_segment": _EXCHANGE_SEGMENTS.get(exchange, exchange),
        "security_id": str(security_id),
        "LTP": float(ltp),
        "timestamp": datetime.fromtimestamp(
            epoch - _DHAN_WS_EPOCH_IST_BIAS_SECONDS, timezone.utc,
        ).isoformat().replace("+00:00", "Z"),
    }
    if fields:
        tick.update(fields)
        if "close" in fields:
            tick["previous_close"] = fields["close"]
    return tick