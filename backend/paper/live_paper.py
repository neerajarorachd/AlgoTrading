"""Live paper trading (rulebook 3a, chunk 3). Called from app.py's
candle-close hook, after everything else, inside its own try/except.

- A pattern hit whose key a user has selected (and switched on) gets a row
  in paper_trades_live: opened, or "not_taken" with the reason.
- Open trades move on 1-min candles that start after the hit candle ends.
- Open trades live in the DB; on startup they reload and catch up on
  today's 1-min candles they missed.
- Stretch end: each taken trade is watched past our exit until the first
  opposite swing on its timeframe (from the engine's swing points) or day
  end, then stretch_* is saved. After a restart, today's are watched again;
  an older day's unfinished one is closed at its day end ("restart").
No selections = nothing happens. Never calls a broker.
"""
from __future__ import annotations

import json
import logging
import threading
import time as _time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from db.models import CandleToday
from db.models_paper import PaperSelection, PaperTradeLive
from indicators import AtrState, update_atr
from paper.trade_logic import (PaperSettings, day_end_ts, first_opposite_swing, ist_date, on_candle, open_trade,
                               stretch_end)
from timeframes import candle_duration

logger = logging.getLogger(__name__)
_TRADE_FIELDS = {c.name for c in PaperTradeLive.__table__.columns} - {"id", "created_at", "updated_at"}
_SELECTION_TTL = 30  # seconds between selection reloads


def _naive_utc(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def wilder_atr(rows: List[dict], period: int) -> Optional[float]:
    state, value = AtrState(), None
    for r in rows:
        value = update_atr(state, float(r["high"]), float(r["low"]), float(r["close"]), period)
    return value


class LivePaperTrader:
    def __init__(self, session_factory, activity_engine=None):
        self.session_factory = session_factory
        self.engine = activity_engine
        self._lock = threading.Lock()
        self._selections: Dict[str, List[PaperSelection]] = {}  # item_key -> rows (all owners)
        self._loaded_at = 0.0
        self._open: Dict[int, List[dict]] = {}  # instrument_id -> open trade dicts (with "id", "_settings")
        self._stretch: Dict[str, List[dict]] = {}  # symbol -> trades whose stretch end is not known yet

    # ---------------------------------------------------------- selections
    def _refresh_selections(self) -> None:
        if _time.monotonic() - self._loaded_at < _SELECTION_TTL:
            return
        with self.session_factory() as s:
            rows = s.query(PaperSelection).filter_by(enabled=True, item_type="pattern").all()
            s.expunge_all()
        by_key: Dict[str, List[PaperSelection]] = {}
        for r in rows:
            by_key.setdefault(r.item_key, []).append(r)
        self._selections = by_key
        self._loaded_at = _time.monotonic()

    def reload_selections(self) -> None:
        self._loaded_at = 0.0

    # --------------------------------------------------------------- startup
    def load_open_trades(self, now: Optional[datetime] = None) -> int:
        """Reload open trades and replay today's 1-min candles they missed."""
        now = _naive_utc(now or datetime.now(timezone.utc))
        with self.session_factory() as s:
            rows = s.query(PaperTradeLive).filter_by(status="open").all()
            sels = {(r.owner_id, r.item_key): r for r in s.query(PaperSelection).all()}
            for row in rows:
                trade = {c: getattr(row, c) for c in _TRADE_FIELDS}
                trade["id"] = row.id
                sel = sels.get((row.owner_id, row.item_key))
                trade["_settings"] = PaperSettings.from_row(sel) if sel else PaperSettings()
                candles = (s.query(CandleToday).filter(
                    CandleToday.symbol == row.symbol, CandleToday.timeframe == "1min",
                    CandleToday.ts >= row.entry_ts + candle_duration(row.timeframe))
                    .order_by(CandleToday.ts).all())
                closed = False
                for c in candles:
                    if on_candle(trade, ts=c.ts, open_=float(c.open_price), high=float(c.high_price),
                                 low=float(c.low_price), close=float(c.close_price), settings=trade["_settings"]):
                        closed = True
                        break
                if not closed and ist_date(trade["entry_ts"]) != ist_date(now) and candles:
                    last = candles[-1]  # an old day with no day-end candle seen: close at the last one
                    from paper.trade_logic import close_trade
                    close_trade(trade, exit_ts=last.ts, exit_price=float(last.close_price), reason="day_end",
                                slippage_pct=trade["_settings"].slippage_pct)
                    closed = True
                self._write(s, row, trade)
                if not closed:
                    self._open.setdefault(row.instrument_id, []).append(trade)
            s.commit()
        self._load_stretch(now)
        return sum(len(v) for v in self._open.values())

    def _load_stretch(self, now: datetime) -> None:
        with self.session_factory() as s:
            rows = (s.query(PaperTradeLive).filter(PaperTradeLive.status != "not_taken",
                                                   PaperTradeLive.stretch_reason.is_(None)).all())
            sels = {(r.owner_id, r.item_key): r for r in s.query(PaperSelection).all()}
            for row in rows:
                trade = {c: getattr(row, c) for c in _TRADE_FIELDS}
                trade["id"] = row.id
                sel = sels.get((row.owner_id, row.item_key))
                trade["_settings"] = PaperSettings.from_row(sel) if sel else PaperSettings()
                start = row.hit_ts + candle_duration(row.timeframe)
                trade["_m1"] = [(c.ts, float(c.high_price), float(c.low_price)) for c in s.query(CandleToday).filter(
                    CandleToday.symbol == row.symbol, CandleToday.timeframe == "1min",
                    CandleToday.ts >= start).order_by(CandleToday.ts)]
                if ist_date(row.hit_ts) != ist_date(now):
                    self._finish_stretch(s, trade, day_end_ts(row.hit_ts, trade["_settings"].day_end), "restart")
                else:
                    self._stretch.setdefault(row.symbol, []).append(trade)
            s.commit()

    # ------------------------------------------------------------------ live
    def on_candle_closed(self, symbol: str, exchange_segment: str, candle, activities: List[dict]) -> None:
        with self._lock:
            if candle.timeframe == "1min":
                self._advance(candle, activities, symbol, exchange_segment)
            if self._stretch.get(symbol):
                try:  # a stretch failure must never stop exits or new hits
                    self._track_stretch(symbol, exchange_segment, candle)
                except Exception:
                    logger.exception("paper stretch tracking failed for %s", symbol)
            hits = [a for a in activities if a.get("activity_type") == "pattern"]
            if hits:
                self._refresh_selections()
                hits = [a for a in hits if a["activity"] in self._selections]
            if hits:
                self._record_hits(symbol, exchange_segment, hits)

    def _advance(self, candle, activities, symbol, exchange_segment) -> None:
        instrument_id = self._instrument_id(symbol, exchange_segment, activities)
        trades = self._open.get(instrument_id) if instrument_id is not None else None
        if not trades:
            return
        ts = _naive_utc(candle.timestamp)
        done = []
        for t in trades:
            if ts < t["entry_ts"] + candle_duration(t["timeframe"]):
                continue  # still inside the hit candle
            if on_candle(t, ts=ts, open_=candle.open, high=candle.high, low=candle.low,
                         close=candle.close, settings=t["_settings"]):
                done.append(t)
        if done:
            with self.session_factory() as s:
                for t in done:
                    self._write(s, s.get(PaperTradeLive, t["id"]), t)
                s.commit()
            self._open[instrument_id] = [t for t in trades if t not in done]

    def _instrument_id(self, symbol, exchange_segment, activities) -> Optional[int]:
        for a in activities:
            if a.get("instrument_id") is not None:
                return a["instrument_id"]
        for trades in self._open.values():
            for t in trades:
                if t["symbol"] == symbol:
                    return t["instrument_id"]
        return None

    def _direction(self, key: str) -> Optional[str]:
        evaluator = getattr(self.engine, "pattern_evaluator", None)
        meta = evaluator.meta(key) if evaluator else None
        kind = meta[1] if meta else None
        return {"bullish": "buy", "bearish": "sell"}.get(kind)

    def _atr(self, symbol, exchange_segment, hit_tf, settings: PaperSettings) -> Optional[float]:
        if self.engine is None:
            return None
        tf = hit_tf if settings.atr_timeframe == "hit" else settings.atr_timeframe
        rows = self.engine.recent_rows(symbol, exchange_segment, tf, settings.atr_period * 4)
        return wilder_atr(rows, settings.atr_period) if len(rows) > settings.atr_period else None

    def _record_hits(self, symbol, exchange_segment, hits) -> None:
        with self.session_factory() as s:
            for a in hits:
                direction = self._direction(a["activity"])
                for sel in self._selections[a["activity"]]:
                    settings = PaperSettings.from_row(sel)
                    already = any(t["item_key"] == a["activity"] and t["owner_id"] == sel.owner_id
                                  for t in self._open.get(a["instrument_id"], []))
                    atr = self._atr(symbol, exchange_segment, a["timeframe"], settings)
                    trade = open_trade(hit_ts=_naive_utc(a["ts"]), direction=direction or "",
                                       hit_close=float(a["close_price"]), atr=atr, settings=settings,
                                       already_open=already)
                    trade.update(owner_id=sel.owner_id, item_type="pattern", item_key=a["activity"],
                                 instrument_id=a["instrument_id"], symbol=symbol, timeframe=a["timeframe"],
                                 direction=direction or "none",
                                 match_pct=a.get("intensity"),
                                 snapshot=json.dumps({"atr_period": settings.atr_period,
                                                      "atr_timeframe": settings.atr_timeframe}))
                    row = PaperTradeLive()
                    self._write(s, row, trade)
                    s.add(row)
                    s.flush()
                    if trade["status"] == "open":
                        trade["id"], trade["_settings"] = row.id, settings
                        self._open.setdefault(a["instrument_id"], []).append(trade)
                        watch = dict(trade, _m1=[])
                        self._stretch.setdefault(symbol, []).append(watch)
            s.commit()

    # --------------------------------------------------------- stretch end
    def _track_stretch(self, symbol, exchange_segment, candle) -> None:
        ts = _naive_utc(candle.timestamp)
        done = []
        for t in self._stretch[symbol]:
            tf = candle_duration(t["timeframe"])
            de = day_end_ts(t["hit_ts"], t["_settings"].day_end)
            if candle.timeframe == "1min":
                if ts >= de:
                    done.append((t, de, "day_end"))
                    continue
                if ts >= t["hit_ts"] + tf:
                    t["_m1"].append((ts, candle.high, candle.low))
            if candle.timeframe == t["timeframe"] and self.engine is not None:
                swings = [{"timeframe": t["timeframe"], "kind": p.kind, "price": p.price,
                           "ts": _naive_utc(p.candle.timestamp)}
                          for p in self.engine.get_swing_points(symbol, exchange_segment, t["timeframe"])]
                sw = first_opposite_swing(t["direction"], swings, t["hit_ts"], t["timeframe"])
                if sw is not None:
                    done.append((t, sw["ts"] + tf, "swing"))
        if done:
            with self.session_factory() as s:
                for t, end, reason in done:
                    self._finish_stretch(s, t, end, reason)
                s.commit()
            finished = {id(t) for t, _, _ in done}
            self._stretch[symbol] = [t for t in self._stretch[symbol] if id(t) not in finished]

    def _finish_stretch(self, session, t: dict, end: datetime, reason: str) -> None:
        start = t["hit_ts"] + candle_duration(t["timeframe"])
        fields = stretch_end(t["direction"], t["entry_price"], t["_m1"], start, end)
        fields["stretch_reason"] = reason
        row = session.get(PaperTradeLive, t["id"])
        if row is not None:
            self._write(session, row, fields)

    @staticmethod
    def _write(session, row, trade: dict) -> None:
        for k, v in trade.items():
            if k in _TRADE_FIELDS:
                setattr(row, k, v)
