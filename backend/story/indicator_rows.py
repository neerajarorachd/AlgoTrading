"""Engine-shaped indicator rows from plain candles, for offline runs.

Live, the story tracker reads ActivityEngine's own per-candle rows
(`_frame_rows`). Offline (research, tests) there is no engine, so this
rebuilds the same fields with the same indicator functions and periods:
RSI 14, MACD 12/26/9, Stochastic 5/3/3, ATR 14, ADX 14, EMA 5/14/21/50,
MA21/MA50 (simple), BB 20 x 2, VWAP reset each day.
"""
from __future__ import annotations

import statistics
from collections import deque
from datetime import datetime, timezone
from typing import Iterable, List, Sequence

from activity_engine import _BB_PERIOD, _MA_LONG_PERIOD, _MA_SHORT_PERIOD, compute_bollinger
from indicators import (
    AdxState, AtrState, EmaState, MacdState, RsiState, StochasticState,
    update_adx, update_atr, update_ema, update_macd, update_rsi, update_stochastic,
)

IST_OFFSET_S = 19800


def _to_dt(ts) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def rows_from_candles(candles: Iterable[Sequence]) -> List[dict]:
    """candles: (ts, open, high, low, close, volume), oldest first; ts is a
    UTC datetime or epoch seconds. Returns one dict per candle with the same
    keys as ActivityEngine's frame rows (None during warm-up)."""
    rsi_s, macd_s, stoch_s = RsiState(), MacdState(), StochasticState()
    atr_s, adx_s = AtrState(), AdxState()
    ema_s = {p: EmaState() for p in (5, 14, 21, 50)}
    closes: deque = deque(maxlen=_MA_LONG_PERIOD)
    vwap_day = None
    cum_pv = cum_vol = 0.0
    out: List[dict] = []
    for ts, o, h, l, c, v in candles:
        dt = _to_dt(ts)
        if dt.date() != vwap_day:  # same rule as ActivityEngine._update_vwap
            vwap_day, cum_pv, cum_vol = dt.date(), 0.0, 0.0
        if v:
            cum_pv += (h + l + c) / 3 * v
            cum_vol += v
        vwap = cum_pv / cum_vol if cum_vol > 0 else None
        closes.append(c)
        bb = compute_bollinger(list(closes)[-_BB_PERIOD:])
        rsi = update_rsi(rsi_s, c)
        macd_line, macd_signal = update_macd(macd_s, c)
        stoch_k, stoch_d = update_stochastic(stoch_s, h, l, c)
        atr = update_atr(atr_s, h, l, c)
        adx, plus_di, minus_di = update_adx(adx_s, h, l, c)
        emas = {p: update_ema(ema_s[p], c, period=p) for p in ema_s}
        full = len(closes) >= _MA_LONG_PERIOD
        out.append({
            "ts": dt, "open": o, "high": h, "low": l, "close": c, "volume": v,
            "rsi": rsi, "macd_line": macd_line, "macd_signal": macd_signal,
            "stoch_k": stoch_k, "stoch_d": stoch_d, "vwap": vwap,
            "ma21": statistics.fmean(list(closes)[-_MA_SHORT_PERIOD:]) if full else None,
            "ma50": statistics.fmean(closes) if full else None,
            "atr": atr,
            "bb_upper": bb.upper if bb else None, "bb_middle": bb.middle if bb else None,
            "bb_lower": bb.lower if bb else None,
            "ema5": emas[5], "ema14": emas[14], "ema21": emas[21], "ema50": emas[50],
            "adx": adx, "plus_di": plus_di, "minus_di": minus_di,
        })
    return out


def ist_date(ts):
    """The trading day of a row's timestamp (IST calendar date)."""
    dt = _to_dt(ts)
    return datetime.fromtimestamp(dt.timestamp() + IST_OFFSET_S, tz=timezone.utc).date()
