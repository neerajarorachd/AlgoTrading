from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from market_state import build_tick_payload


class MarketFeed:
    """Own subscriptions and translate broker ticks into frontend ticks."""

    def __init__(self, broker, on_tick: Callable[[dict], None]):
        self.broker = broker
        self.on_tick = on_tick
        self._instruments: Dict[str, dict] = {}

    def start(self) -> None:
        self.broker.connect()

    def subscribe(self, instrument: dict) -> None:
        self._instruments[self._key(instrument)] = instrument
        self.broker.subscribe_feed([instrument], self._on_broker_tick)

    def unsubscribe(self, instrument: dict) -> None:
        self._instruments.pop(self._key(instrument), None)
        self.broker.unsubscribe_feed([instrument])

    def stop(self) -> None:
        self.broker.disconnect()

    def _on_broker_tick(self, raw_tick: dict) -> None:
        instrument = self._find_instrument(raw_tick)
        if instrument is None:
            return

        ltp = raw_tick.get("LTP", raw_tick.get("last_price"))
        previous_close = raw_tick.get("PreviousClose", raw_tick.get("previous_close", instrument.get("previous_close")))
        if ltp is None or previous_close is None:
            return

        timestamp = raw_tick.get("Timestamp", raw_tick.get("timestamp"))
        if isinstance(timestamp, datetime):
            timestamp = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        self.on_tick(build_tick_payload(
            symbol=instrument["symbol"],
            exchange=instrument.get("exchange", self._exchange_name(instrument["exchange_segment"])),
            segment=instrument.get("segment", "EQUITY"),
            ltp=float(ltp),
            previous_close=float(previous_close),
            currency=instrument.get("currency", "INR"),
            ts=timestamp,
        ))

    def _find_instrument(self, raw_tick: dict) -> Optional[dict]:
        security_id = str(raw_tick.get("SecurityId", raw_tick.get("security_id", "")))
        exchange_segment = raw_tick.get("ExchangeSegment", raw_tick.get("exchange_segment"))
        for instrument in self._instruments.values():
            if str(instrument["security_id"]) == security_id and (
                exchange_segment is None or instrument["exchange_segment"] == exchange_segment
            ):
                return instrument
        return None

    @staticmethod
    def _key(instrument: dict) -> str:
        return f"{instrument['exchange_segment']}:{instrument['security_id']}"

    @staticmethod
    def _exchange_name(exchange_segment: str) -> str:
        return exchange_segment.split("_", 1)[0]