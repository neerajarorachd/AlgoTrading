"""Manual trade engine (rulebook 3b). Places, watches and closes manual
trades; paper only (live orders are refused: "coming soon").

Prices: no tick listener of its own. The existing tick path (app.py's
candle-tick callback) calls `offer_price` for every tick; only stocks with an
open or pending trade go on the internal queue, everything else is one set
lookup. One worker thread drains the queue (keeping the latest price per
stock) and moves the trades: limit fills, stop, target, best/worst, and the
15:10 IST intraday square-off (also checked on a timer, for a stock whose
ticks have stopped).

Restart: open/pending trades reload from the DB and catch up on the 1-min
candles they missed (candles_today).

Snapshots: at entry and at exit, the live engine's last closed candle on
1/3/5-min (indicators, volume, pattern colour) -> trade_indicator_snapshots.
No broker call, ever, from this module.
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from db.models import CandleToday
from db.models_trades import PAPER_CAPITAL_DEFAULT, Trade, TradeAccount, TradeIndicatorSnapshot, TradeOrder
from expiry_calendar import expiry_context
from trades import rules

logger = logging.getLogger(__name__)

TIMEFRAMES = ("1min", "3min", "5min")
_FIELDS = [c.name for c in Trade.__table__.columns if c.name not in ("id", "created_at", "updated_at")]
_SNAP_FIELDS = ("rsi", "macd_line", "macd_signal", "stoch_k", "stoch_d", "adx", "plus_di", "minus_di", "vwap",
                "ema5", "ema14", "ema21", "ema50", "ma21", "ma50", "bb_upper", "bb_middle", "bb_lower", "atr")
Key = Tuple[str, str]  # (symbol, exchange_segment)


class OrderError(ValueError):
    """A request the engine refuses; the message is shown to the user."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _f(v) -> Optional[float]:
    return None if v is None else float(v)


def to_dict(row: Trade) -> dict:
    d = {c: getattr(row, c) for c in _FIELDS}
    d["id"] = row.id
    return d


class TradeEngine:
    def __init__(self, session_factory, activity_engine=None,
                 price_lookup: Optional[Callable[[str, str], Optional[float]]] = None,
                 broadcast: Optional[Callable[[dict], None]] = None,
                 now_fn: Callable[[], datetime] = _utcnow):
        self.session_factory = session_factory
        self.activity_engine = activity_engine
        self.price_lookup = price_lookup      # (symbol, exchange_segment) -> last LTP
        self.broadcast = broadcast            # trade dict -> socket event
        self.now = now_fn
        self._lock = threading.RLock()
        self._active: Dict[Key, List[dict]] = {}   # open + pending trades by stock
        self.watched: set = set()                  # keys with active trades (read in the tick path)
        self._queue: "queue.Queue[tuple]" = queue.Queue(maxsize=20000)
        self._last_price: Dict[Key, float] = {}
        self._last_flush: Optional[datetime] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------- tick path
    def offer_price(self, symbol: str, exchange_segment: str, price: float, ts: datetime) -> None:
        """Called on every tick: cheap, never raises, never blocks."""
        key = (symbol, exchange_segment)
        if key not in self.watched:
            return
        try:
            self._queue.put_nowait((key, float(price), ts))
        except queue.Full:
            pass  # the worker keeps only the latest price anyway

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="trade-engine")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            latest: Dict[Key, tuple] = {}
            try:
                key, price, ts = self._queue.get(timeout=15)
                latest[key] = (price, ts)
                while True:  # drain: keep the newest price per stock
                    key, price, ts = self._queue.get_nowait()
                    latest[key] = (price, ts)
            except queue.Empty:
                pass
            for key, (price, ts) in latest.items():
                try:
                    self.process_price(key[0], key[1], price, ts)
                except Exception:
                    logger.exception("trade engine: price %s failed", key)
            try:
                self.check_day_end()
            except Exception:
                logger.exception("trade engine: day-end check failed")

    # --------------------------------------------------------------- moving
    def process_price(self, symbol: str, exchange_segment: str, price: float, ts: datetime) -> None:
        key = (symbol, exchange_segment)
        ts = rules.naive_utc(ts)
        with self._lock:
            self._last_price[key] = price
            trades = list(self._active.get(key, []))
            for t in trades:
                before = (t.get("best_price"), t.get("worst_price"))
                what = rules.on_price(t, ts=ts, price=price)
                if what == "filled":
                    self._set_expiry(t, ts)
                    self._save(t, snapshot="entry")
                elif what == "closed":
                    self._save(t, snapshot="exit" if t["status"] == "closed" else None)
                elif before != (t.get("best_price"), t.get("worst_price")):
                    t["_dirty"] = True

    def check_day_end(self, now: Optional[datetime] = None) -> None:
        """Square off intraday trades at 15:10 IST even without a new tick."""
        now = now or self.now()
        flush_dirty = self._last_flush is None or (now - self._last_flush).total_seconds() >= 60
        if flush_dirty:
            self._last_flush = now
        with self._lock:
            for key, trades in list(self._active.items()):
                price = self._price(key)
                for t in list(trades):
                    if t["product"] == "delivery":
                        continue
                    if t["status"] == "pending" and rules.ist_hhmm(now) >= rules.DAY_END_HHMM:
                        t.update(status="cancelled", exit_ts=now, exit_reason="day_end")
                        self._save(t)
                    elif t["status"] == "open" and rules.is_day_end(t, now) and price is not None:
                        rules.close_trade(t, ts=now, price=price, reason="day_end")
                        self._save(t, snapshot="exit")
                    elif flush_dirty and t.pop("_dirty", False):
                        self._save(t)  # persist best/worst once a minute

    def _price(self, key: Key) -> Optional[float]:
        if self.price_lookup is not None:
            try:
                p = self.price_lookup(*key)
                if p is not None:
                    return float(p)
            except Exception:
                logger.exception("trade engine: price lookup failed for %s", key)
        return self._last_price.get(key)

    # ------------------------------------------------------------- orders
    def place_order(self, *, owner_id: int, instrument, direction: str, quantity: int, product: str = "intraday",
                    entry_type: str = "market", limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None, target_price: Optional[float] = None,
                    mode: str = "paper", source: str = "grid", note: Optional[str] = None,
                    screen_signal: Optional[str] = None, slippage_pct: float = 0.05) -> dict:
        if mode == "live":
            raise OrderError("Live trading coming soon")
        if mode != "paper":
            raise OrderError("mode must be paper or live")
        if direction not in ("buy", "sell"):
            raise OrderError("direction must be buy or sell")
        if product not in ("intraday", "delivery"):
            raise OrderError("product must be intraday or delivery")
        if direction == "sell" and product == "delivery":
            raise OrderError("a sell-first (short) trade must be intraday")
        if entry_type not in ("market", "limit"):
            raise OrderError("entry type must be market or limit")
        if source not in ("grid", "chart", "ticket"):
            raise OrderError("source must be grid, chart or ticket")
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            raise OrderError("quantity must be a whole number")
        if quantity < 1:
            raise OrderError("quantity must be at least 1")
        now = self.now()
        err = rules.entry_window_error(product, now)
        if err:
            raise OrderError(err)
        key = (instrument.symbol, instrument.exchange_segment)
        ltp = self._price(key)
        if entry_type == "market" and ltp is None:
            raise OrderError("no live price for this stock yet")
        if entry_type == "limit":
            if limit_price is None or float(limit_price) <= 0:
                raise OrderError("limit price is required for a limit order")
            limit_price = float(limit_price)
        stop_price = _f(stop_price)
        target_price = _f(target_price)
        ref = limit_price if entry_type == "limit" else ltp
        err = rules.validate_levels(direction, ref, stop_price, target_price)
        if err:
            raise OrderError(err)

        trade = {f: None for f in _FIELDS}
        trade.update(owner_id=owner_id, mode=mode, source=source, instrument_id=instrument.id,
                     symbol=instrument.symbol, exchange_segment=instrument.exchange_segment,
                     direction=direction, product=product, quantity=quantity, entry_type=entry_type,
                     limit_price=limit_price, status="pending", placed_ts=now, ltp_at_order=ltp,
                     stop_price=stop_price, target_price=target_price, initial_stop_price=stop_price,
                     initial_target_price=target_price, slippage_pct=float(slippage_pct),
                     note=(note or None), screen_signal=screen_signal)
        with self._lock:
            acct = self.account(owner_id, mode)
            if rules.capital_used(trade) > acct["free"] + 1e-6:
                raise OrderError(f"not enough paper capital: free Rs {acct['free']:,.0f}, "
                                 f"order needs Rs {rules.capital_used(trade):,.0f}")
            self._set_expiry(trade, now)
            snapshot = None
            if entry_type == "market":
                rules.fill_entry(trade, ts=now, price=ltp)
                snapshot = "entry"
            elif ltp is not None and rules.limit_fill_price(trade, ltp) is not None:
                rules.fill_entry(trade, ts=now, price=ltp)  # marketable limit: fills at once
                snapshot = "entry"
            self._save(trade, snapshot=snapshot, new=True)
        return self._public(trade)

    def modify(self, owner_id: int, trade_id: int, *, stop_price=..., target_price=...) -> dict:
        with self._lock:
            t = self._find(owner_id, trade_id)
            ref = t["entry_price"] if t["status"] == "open" else t["limit_price"]
            new_stop = t["stop_price"] if stop_price is ... else _f(stop_price)
            new_target = t["target_price"] if target_price is ... else _f(target_price)
            if t["status"] == "open":
                # an open trade's levels are judged against the current price
                ref = self._price((t["symbol"], t["exchange_segment"])) or ref
            err = rules.validate_levels(t["direction"], ref, new_stop, new_target)
            if err:
                raise OrderError(err)
            self._check_capital(t, stop_price=new_stop, target_price=new_target)
            t.update(stop_price=new_stop, target_price=new_target)
            self._save(t)
            return self._public(t)

    def exit_now(self, owner_id: int, trade_id: int, exit_type: str = "market",
                 limit_price: Optional[float] = None) -> dict:
        """Market: close now at the current price (cancels a pending order).
        Limit: set / change a waiting limit exit; if the price already reaches
        it, it fills at once at the current price."""
        if exit_type not in ("market", "limit"):
            raise OrderError("exit type must be market or limit")
        with self._lock:
            t = self._find(owner_id, trade_id)
            if t["status"] == "pending":
                t.update(status="cancelled", exit_ts=self.now(), exit_reason="cancelled")
                self._save(t)
                return self._public(t)
            price = self._price((t["symbol"], t["exchange_segment"]))
            if exit_type == "limit":
                lim = _f(limit_price)
                if lim is None or lim <= 0:
                    raise OrderError("limit price is required for a limit exit")
                self._check_capital(t, exit_limit_price=lim, target_price=None)
                t["exit_limit_price"] = lim
                t["target_price"] = None  # the limit exit replaces the target leg; the stop stays
                if price is not None and rules.exit_limit_reached(t, price):
                    rules.close_trade(t, ts=self.now(), price=price, reason="limit")
                    self._save(t, snapshot="exit")
                else:
                    self._save(t)
                return self._public(t)
            if price is None:
                raise OrderError("no live price to exit at")
            rules.close_trade(t, ts=self.now(), price=price, reason="manual")
            self._save(t, snapshot="exit")
            return self._public(t)

    def cancel_exit_limit(self, owner_id: int, trade_id: int) -> dict:
        with self._lock:
            t = self._find(owner_id, trade_id)
            t["exit_limit_price"] = None
            self._save(t)
            return self._public(t)

    def _check_capital(self, t: dict, **changes) -> None:
        """Refuse a change that adds an exit leg the free capital can't cover
        (a second leg blocks the trade's value again)."""
        after = rules.capital_used({**t, **changes})
        extra = after - rules.capital_used(t)
        if extra > 0:
            free = self.account(t["owner_id"], t["mode"])["free"]
            if extra > free + 1e-6:
                raise OrderError(f"not enough paper capital for a second exit leg: needs Rs {extra:,.0f} more, "
                                 f"free Rs {free:,.0f}")

    def cancel_order(self, owner_id: int, order_id: int) -> dict:
        """Cancel one pending order from the Pending grid: an entry cancels its
        trade; a stop / target leg removes that level (the trade stays open);
        a limit exit goes back to no exit order."""
        with self.session_factory() as s:
            o = s.get(TradeOrder, order_id)
            if o is None or o.owner_id != owner_id or o.status != "pending":
                raise OrderError("no pending order with that id")
            kind, trade_id = o.kind, o.trade_id
        if kind == "entry":
            return self.exit_now(owner_id, trade_id)
        if kind == "stop":
            return self.modify(owner_id, trade_id, stop_price=None)
        if kind == "target":
            return self.modify(owner_id, trade_id, target_price=None)
        return self.cancel_exit_limit(owner_id, trade_id)

    # OCO wording on the leg that did not fill, by how the trade ended
    _CLOSE_NOTE = {"stop": "OCO: stop filled", "target": "OCO: target filled", "limit": "OCO: limit exit filled",
                   "manual": "manual exit", "day_end": "day-end square-off"}

    def _sync_orders(self, s, t: dict) -> None:
        """Bring the trade's order rows in line with the trade (one query).
        entry: pending while the trade is, then filled (or cancelled).
        stop / target / exit (limit exit): pending while the trade is open and
        the level is set; when the level goes the order is cancelled, unless
        the trade closed on it -- then it is filled at the exit price.
        A market exit (manual / day end) gets its own filled exit row."""
        orders = s.query(TradeOrder).filter(TradeOrder.trade_id == t["id"]).all()
        now = self.now()
        leg_side = "sell" if t["direction"] == "buy" else "buy"
        status = t["status"]

        def add(kind, order_type, side, price, st, **kw):
            o = TradeOrder(trade_id=t["id"], owner_id=t["owner_id"], symbol=t["symbol"], kind=kind,
                           order_type=order_type, side=side, quantity=t["quantity"], price=price, status=st,
                           placed_ts=kw.pop("placed_ts", now), **kw)
            s.add(o)
            orders.append(o)
            return o

        # entry
        entry = next((o for o in orders if o.kind == "entry"), None)
        if entry is None:
            entry = add("entry", t["entry_type"], t["direction"], t["limit_price"], "pending", placed_ts=t["placed_ts"])
        if entry.status == "pending":
            if t.get("entry_price") is not None:
                entry.status, entry.fill_price, entry.done_ts = "filled", t["entry_price"], t["entry_ts"]
            elif status == "cancelled":
                entry.status, entry.done_ts = "cancelled", t.get("exit_ts") or now
                entry.cancel_reason = "day end" if t.get("exit_reason") == "day_end" else "cancelled by user"
        if t.get("entry_price") is None:
            return  # legs only exist once the entry has filled

        reason = t.get("exit_reason")
        levels = {"stop": ("sl_m", t.get("stop_price")), "target": ("limit", t.get("target_price")),
                  "exit": ("limit", t.get("exit_limit_price"))}
        filled_by = {"stop": "stop", "target": "target", "exit": "limit"}
        for kind, (otype, level) in levels.items():
            pend = [o for o in orders if o.kind == kind and o.status == "pending" and o.order_type == otype]
            if status == "open" and level is not None:
                if pend:
                    pend[0].price = level
                else:
                    add(kind, otype, leg_side, level, "pending", placed_ts=t["entry_ts"] if kind != "exit" else now)
                continue
            for o in pend:
                o.done_ts = t.get("exit_ts") or now
                if status == "closed" and reason == filled_by[kind]:
                    o.status, o.fill_price = "filled", t["exit_price"]
                else:
                    o.status = "cancelled"
                    if status == "closed":
                        o.cancel_reason = self._CLOSE_NOTE.get(reason, "trade closed")
                    elif kind == "target" and t.get("exit_limit_price") is not None:
                        o.cancel_reason = "replaced by limit exit"
                    else:
                        o.cancel_reason = "cancelled by user"
        # a market exit (manual / day end) is its own filled order
        if status == "closed" and reason in ("manual", "day_end") and not any(
                o.kind == "exit" and o.order_type == "market" for o in orders):
            add("exit", "market", leg_side, None, "filled", placed_ts=t["exit_ts"], done_ts=t["exit_ts"],
                fill_price=t["exit_price"], cancel_reason=None)

    def orders_for(self, owner_id: int, trade_id: Optional[int] = None, status: Optional[str] = None) -> List[dict]:
        cols = [c.name for c in TradeOrder.__table__.columns]
        with self.session_factory() as s:
            q = s.query(TradeOrder).filter(TradeOrder.owner_id == owner_id)
            if trade_id is not None:
                q = q.filter(TradeOrder.trade_id == trade_id)
            if status is not None:
                q = q.filter(TradeOrder.status == status)
            return [{c: getattr(o, c) for c in cols} for o in q.order_by(TradeOrder.placed_ts, TradeOrder.id).all()]

    def _find(self, owner_id: int, trade_id: int) -> dict:
        for trades in self._active.values():
            for t in trades:
                if t["id"] == trade_id and t["owner_id"] == owner_id:
                    return t
        raise OrderError("no open or pending trade with that id")

    # ------------------------------------------------------------ reading
    def account(self, owner_id: int, mode: str = "paper") -> dict:
        """Capital, used (open + pending trades), realised net P&L, free."""
        with self.session_factory() as s:
            acct = s.query(TradeAccount).filter_by(owner_id=owner_id, mode=mode).one_or_none()
            if acct is None:
                acct = TradeAccount(owner_id=owner_id, mode=mode, starting_capital=PAPER_CAPITAL_DEFAULT)
                s.add(acct)
                s.commit()
            start = float(acct.starting_capital)
            closed = s.query(Trade.net_pnl, Trade.gross_pnl, Trade.exit_ts).filter(
                Trade.owner_id == owner_id, Trade.mode == mode, Trade.status == "closed").all()
            realised = sum(float(r.net_pnl or 0.0) for r in closed)
            # booked today (IST): the realised half of the Today's P&L tile
            today = rules.ist_date(self.now())
            today_rows = [r for r in closed if r.exit_ts is not None and rules.ist_date(r.exit_ts) == today]
            realised_today = sum(float(r.net_pnl or 0.0) for r in today_rows)
            realised_today_gross = sum(float(r.gross_pnl or 0.0) for r in today_rows)
        with self._lock:
            active = [t for ts in self._active.values() for t in ts if t["owner_id"] == owner_id and t["mode"] == mode]
            used = sum(rules.capital_used(t) for t in active)
            unrealised = sum(self._running(t)["net_pnl"] for t in active if t["status"] == "open"
                             and self._running(t) is not None)
        return {"mode": mode, "starting_capital": start, "realised_pnl": round(realised, 2),
                "realised_today_pnl": round(realised_today, 2), "realised_today_gross": round(realised_today_gross, 2),
                "unrealised_pnl": round(unrealised, 2), "used": round(used, 2),
                "free": round(start + realised - used, 2)}

    def _running(self, t: dict) -> Optional[dict]:
        """Cheap running P&L: gross from the price, net = gross - est_costs."""
        price = self._price((t["symbol"], t["exchange_segment"]))
        if price is None or t.get("entry_price") is None:
            return None
        sign = 1 if t["direction"] == "buy" else -1
        gross = sign * (price - t["entry_price"]) * t["quantity"]
        return {"ltp": price, "gross_pnl": round(gross, 2), "net_pnl": round(gross - (t.get("est_costs") or 0.0), 2)}

    def detail(self, owner_id: int, trade_id: int) -> Optional[dict]:
        """One active trade with its EXACT expenses at the current price (the row-click panel)."""
        with self._lock:
            try:
                t = self._find(owner_id, trade_id)
            except OrderError:
                return None
            d = self._public(t)
            price = self._price((t["symbol"], t["exchange_segment"]))
            if t["status"] == "open" and price is not None:
                d["exact"] = {"ltp": price, **rules.pnl(t, price)}
            return d

    def _public(self, t: dict) -> dict:
        d = {k: v for k, v in t.items() if not k.startswith("_")}
        if t["status"] == "open":
            d["running"] = self._running(t)
        return d

    def active_trades(self, owner_id: int) -> List[dict]:
        with self._lock:
            return [self._public(t) for ts in self._active.values() for t in ts if t["owner_id"] == owner_id]

    # ------------------------------------------------------------ storing
    def _set_expiry(self, t: dict, ts: datetime) -> None:
        t.update(expiry_context(rules.ist_date(ts)))

    def _save(self, t: dict, snapshot: Optional[str] = None, new: bool = False) -> None:
        """Write the trade row (and a snapshot), keep the in-memory index right, tell the UI."""
        t.pop("_dirty", None)
        with self.session_factory() as s:
            if new:
                row = Trade(**{f: t[f] for f in _FIELDS})
                s.add(row)
                s.flush()
                t["id"] = row.id
            else:
                row = s.get(Trade, t["id"])
                for f in _FIELDS:
                    setattr(row, f, t[f])
            self._sync_orders(s, t)
            if snapshot:
                ts = t["entry_ts"] if snapshot == "entry" else t["exit_ts"]
                price = t["entry_price"] if snapshot == "entry" else t["exit_price"]
                for snap in self.snapshot_rows(t["symbol"], t["exchange_segment"]):
                    s.add(TradeIndicatorSnapshot(trade_id=t["id"], event=snapshot, event_ts=ts, price=price, **snap))
            s.commit()
        key = (t["symbol"], t["exchange_segment"])
        lst = self._active.setdefault(key, [])
        if t["status"] in ("open", "pending"):
            if t not in lst:
                lst.append(t)
        elif t in lst:
            lst.remove(t)
        if not lst:
            self._active.pop(key, None)
        self.watched = set(self._active)
        if self.broadcast is not None:
            try:
                self.broadcast(self._public(t))
            except Exception:
                logger.exception("trade engine: broadcast failed")

    def snapshot_rows(self, symbol: str, exchange_segment: str) -> List[dict]:
        """Last closed candle per timeframe from the live engine: indicators,
        volume vs its 20-candle average, pattern colour. Empty when the
        engine has nothing yet (e.g. before the first candle closes)."""
        eng = self.activity_engine
        if eng is None:
            return []
        evaluator = getattr(eng, "pattern_evaluator", None)
        out = []
        for tf in TIMEFRAMES:
            try:
                rows = eng.recent_rows(symbol, exchange_segment, tf, 21)
            except Exception:
                logger.exception("trade engine: recent_rows failed")
                rows = []
            if not rows:
                continue
            last = rows[-1]
            snap = {"timeframe": tf, "candle_ts": rules.naive_utc(last["ts"])}
            for f in ("open", "high", "low", "close", "volume"):
                snap[f] = _f(last.get(f))
            prev = [r.get("volume") for r in rows[:-1] if r.get("volume") is not None]
            snap["avg_volume_20"] = round(sum(prev) / len(prev), 2) if prev else None
            for f in _SNAP_FIELDS:
                snap[f] = _f(last.get(f))
            if snap["macd_line"] is not None and snap["macd_signal"] is not None:
                snap["macd_hist"] = snap["macd_line"] - snap["macd_signal"]
            if snap["bb_upper"] is not None and snap["bb_lower"] is not None and snap["bb_middle"]:
                snap["bb_width_pct"] = (snap["bb_upper"] - snap["bb_lower"]) / snap["bb_middle"] * 100.0
            if evaluator is not None:
                try:
                    from colour_scoring import colour_for
                    c = colour_for(evaluator.scores.get((symbol, exchange_segment, tf), {}),
                                   evaluator.patterns, evaluator.threshold)
                    snap.update(colour=c["colour"], top_pattern=c["top_pattern"], top_score=c["top_score"])
                except Exception:
                    logger.exception("trade engine: colour snapshot failed")
            out.append(snap)
        return out

    # ------------------------------------------------------------ restart
    def load_active(self) -> int:
        """Reload open/pending trades; replay today's 1-min candles they missed."""
        now = self.now()
        with self._lock, self.session_factory() as s:
            rows = s.query(Trade).filter(Trade.status.in_(("open", "pending"))).all()
            loaded = [to_dict(r) for r in rows]
        n = 0
        with self._lock:
            for t in loaded:
                since = t["entry_ts"] or t["placed_ts"]
                with self.session_factory() as s:
                    candles = (s.query(CandleToday).filter(
                        CandleToday.symbol == t["symbol"], CandleToday.timeframe == "1min",
                        CandleToday.ts >= since).order_by(CandleToday.ts).all())
                    candles = [(c.ts, float(c.open_price), float(c.high_price), float(c.low_price),
                                float(c.close_price)) for c in candles]
                result = None
                for ts, o, h, l, c in candles:
                    if ts < since.replace(second=0, microsecond=0):
                        continue
                    result = rules.on_candle(t, ts=ts, open_=o, high=h, low=l, close=c)
                    if result == "filled":
                        self._set_expiry(t, ts)
                    if t["status"] in ("closed", "cancelled"):
                        break
                # an intraday trade from an earlier day that never saw its day end
                if t["status"] in ("open", "pending") and t["product"] != "delivery" \
                        and rules.ist_date(since) != rules.ist_date(now):
                    if t["status"] == "pending":
                        t.update(status="cancelled", exit_ts=now, exit_reason="day_end")
                    elif candles:
                        rules.close_trade(t, ts=candles[-1][0], price=candles[-1][4], reason="day_end")
                    else:
                        t.update(status="closed", exit_ts=now, exit_reason="day_end",
                                 note=((t.get("note") or "") + " [closed on restart: no candles to price the exit]").strip())
                self._save(t)  # no snapshot: the live engine has no state for missed candles
                if t["status"] in ("open", "pending"):
                    n += 1
        return n
