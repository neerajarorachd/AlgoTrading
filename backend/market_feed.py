from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from depth_metrics import calculate_depth_metrics
from market_state import build_tick_payload


class MarketFeed:
    """Own subscriptions and translate broker ticks into frontend ticks."""

    def __init__(
        self,
        broker,
        on_tick: Callable[[dict], None],
        on_depth: Optional[Callable[[dict], None]] = None,
        on_candle_tick: Optional[Callable[[dict, dict], None]] = None,
        candle_lookup: Optional[Callable[[str, str], Optional[object]]] = None,
    ):
        self.broker = broker
        self.on_tick = on_tick
        self.on_depth = on_depth
        self.on_candle_tick = on_candle_tick
        # plain callback (symbol, exchange_segment) -> last closed 1-min Candle or
        # None, so MarketFeed doesn't need to import/know about CandleAggregator
        # as a concrete type — keeps feed/aggregation cleanly decoupled.
        self.candle_lookup = candle_lookup
        self._instruments: Dict[str, dict] = {}
        self._depth_cache: Dict[str, dict] = {}   # key -> {"buy": [levels], "sell": [levels]}
        self._last_ltp: Dict[str, float] = {}      # key -> last known ltp, needed to join depth packets
        self._last_volume: Dict[str, int] = {}     # key -> last cumulative day volume, for delta calc

    def start(self) -> None:
        self.broker.connect()

    def subscribe(self, instrument: dict) -> None:
        key = self._key(instrument)
        self._instruments[key] = instrument
        if instrument.get("ltp") is not None:
            # seeds _last_ltp so a depth packet arriving before the first live tick
            # can still be joined into a metrics event, instead of being dropped
            self._last_ltp[key] = float(instrument["ltp"])
        self.broker.subscribe_feed([instrument], self._on_broker_tick)

    def unsubscribe(self, instrument: dict) -> None:
        self._instruments.pop(self._key(instrument), None)
        self.broker.unsubscribe_feed([instrument])

    def stop(self) -> None:
        self.broker.disconnect()

    def _on_broker_tick(self, raw_tick: dict) -> None:
        if raw_tick.get("type") == "Full Market Depth":
            self._handle_depth(raw_tick)
            return

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

        payload = build_tick_payload(
            symbol=instrument["symbol"],
            exchange=instrument.get("exchange", self._exchange_name(instrument["exchange_segment"])),
            segment=instrument.get("segment", "EQUITY"),
            ltp=float(ltp),
            previous_close=float(previous_close),
            currency=instrument.get("currency", "INR"),
            ts=timestamp,
        )
        self._add_change_metrics(payload, instrument, float(ltp), float(previous_close))
        key = self._key(instrument)
        self._last_ltp[key] = float(ltp)
        self.on_tick(payload)

        buy_depth = raw_tick.get("buy_depth")
        sell_depth = raw_tick.get("sell_depth")
        if buy_depth and sell_depth and self.on_depth is not None:
            # Full packets carry both sides of the book together, unlike the
            # separate 20-depth feed's one-side-per-packet format handled by
            # _handle_depth — no need for the buy/sell cache merge there.
            metrics = calculate_depth_metrics(float(ltp), buy_depth, sell_depth)
            self.on_depth({
                "type": "depth",
                "symbol": instrument["symbol"],
                "exchange_segment": instrument["exchange_segment"],
                "security_id": instrument["security_id"],
                **metrics,
                "ts": payload["ts"],
            })

        if self.on_candle_tick is not None:
            cumulative_volume = raw_tick.get("volume")
            volume_delta = 0
            if cumulative_volume is not None:
                last_volume = self._last_volume.get(key, cumulative_volume)
                volume_delta = max(0, int(cumulative_volume) - int(last_volume))
                self._last_volume[key] = int(cumulative_volume)
            self.on_candle_tick(instrument, {"ltp": float(ltp), "volume": volume_delta, "ts": payload["ts"]})

    def _add_change_metrics(self, payload: dict, instrument: dict, ltp: float, previous_close: float) -> None:
        """Three extra change figures beyond the base tick contract's LTP-vs-
        previous-close: gap at open, change since today's open, and change since
        the last closed 1-min candle. Each pair is None until its input is known."""
        today_open = instrument.get("open")
        if today_open:
            today_open = float(today_open)
            payload["gap_absolute"] = round(today_open - previous_close, 2)
            payload["gap_percentage"] = round((today_open - previous_close) / previous_close * 100, 2) if previous_close else None
            payload["day_change_absolute"] = round(ltp - today_open, 2)
            payload["day_change_percentage"] = round((ltp - today_open) / today_open * 100, 2) if today_open else None
        else:
            payload["gap_absolute"] = None
            payload["gap_percentage"] = None
            payload["day_change_absolute"] = None
            payload["day_change_percentage"] = None

        prev_candle = None
        if self.candle_lookup is not None:
            prev_candle = self.candle_lookup(instrument["symbol"], instrument["exchange_segment"])
        if prev_candle is not None and prev_candle.close:
            payload["candle_change_absolute"] = round(ltp - prev_candle.close, 2)
            payload["candle_change_percentage"] = round((ltp - prev_candle.close) / prev_candle.close * 100, 2)
        else:
            payload["candle_change_absolute"] = None
            payload["candle_change_percentage"] = None

    def _handle_depth(self, raw_tick: dict) -> None:
        key = f"{raw_tick['exchange_segment']}:{raw_tick['security_id']}"
        instrument = self._instruments.get(key)
        if instrument is None or self.on_depth is None:
            return

        cache = self._depth_cache.setdefault(key, {})
        cache[raw_tick["side"]] = raw_tick["levels"]

        ltp = self._last_ltp.get(key)
        if ltp is None or "buy" not in cache or "sell" not in cache:
            return  # wait for both sides plus at least one prior ticker/quote/full tick

        metrics = calculate_depth_metrics(ltp, cache["buy"], cache["sell"])
        self.on_depth({
            "type": "depth",
            "symbol": instrument["symbol"],
            "exchange_segment": instrument["exchange_segment"],
            "security_id": instrument["security_id"],
            **metrics,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })

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