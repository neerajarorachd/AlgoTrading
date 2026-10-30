"""Opening range breakout on "stocks in play" (Zarattini & Aziz, "Can Day
Trading Really Be Profitable?", SSRN 4416622) on our 13 stocks, 5 min, 2 years.
PRELIMINARY: the paper picks the day's top stocks by relative volume from
thousands; we can only pick from 13.

Rules (the paper's shape):
- opening range = first N minutes (5 or 15); direction = that range's candle
  colour (open -> close); a flat range is skipped;
- entry: price trades through the range high (long) / low (short) later the
  same day, filled at the level (or the open if it gaps through);
- stop: k x the stock's 14-day daily ATR from entry; exit at day end;
- in play: opening-range volume / its own 14-day average >= RV; each day
  take only the top K stocks by that ratio.
Money: Rs 5L pool, leg Rs 2.5L (2 at a time) or Rs 1.25L (4 at a time),
itemized costs + 0.05% slippage."""
import sys
from collections import defaultdict, deque

from mcommon import SEVEN, SYMBOLS, Y2_START, candles, day, minute_of_day

sys.path.insert(0, "D:/AI Trading/AlgoTrading-signals/backend/scripts")
from order_backtest import itemized_round_trip_cost  # noqa: E402

CAPITAL, SLIP = 500_000, 0.05


def by_day(sym):
    days = defaultdict(list)
    for c in candles(sym, "5min"):
        days[day(c[0])].append(c)
    return [(d, sorted(v)) for d, v in sorted(days.items())]


DAYS = {s: by_day(s) for s in SYMBOLS}


def setups(or_min, stop_k):
    """per (sym, day): (rv, trade) where trade = (t0, t1, entry, dir, ret%) or None."""
    n_or = or_min // 5
    out = defaultdict(dict)
    for sym, days in DAYS.items():
        trs = deque(maxlen=14)
        prev_close = None
        or_vols = deque(maxlen=14)
        atr = None
        for d, cs in days:
            if len(cs) < n_or + 2 or minute_of_day(cs[0][0]) != 555:
                prev_close = cs[-1][4]
                continue
            hi, lo = max(c[2] for c in cs), min(c[3] for c in cs)
            orc = cs[:n_or]
            o, c = orc[0][1], orc[-1][4]
            orh, orl = max(x[2] for x in orc), min(x[3] for x in orc)
            orv = sum(x[5] for x in orc)
            rv = orv / (sum(or_vols) / len(or_vols)) if len(or_vols) == 14 and sum(or_vols) else None
            atr = sum(trs) / len(trs) if len(trs) == 14 else None
            trade = None
            if rv is not None and atr and c != o:
                dr = 1 if c > o else -1
                level = orh if dr > 0 else orl
                stop = None
                for k in range(n_or, len(cs)):
                    ts, op, h, l, cl, v = cs[k]
                    if stop is None:
                        if (dr > 0 and h >= level) or (dr < 0 and l <= level):
                            entry = max(op, level) if dr > 0 else min(op, level)
                            stop = entry - dr * stop_k * atr
                            t0 = ts
                            # same candle can also hit the stop
                            if (dr > 0 and l <= stop) or (dr < 0 and h >= stop):
                                trade = (t0, ts, entry, dr, dr * (stop - entry) / entry * 100)
                                break
                        continue
                    if (dr > 0 and l <= stop) or (dr < 0 and h >= stop):
                        px = min(op, stop) if dr > 0 else max(op, stop)
                        trade = (t0, ts, entry, dr, dr * (px - entry) / entry * 100)
                        break
                else:
                    if stop is not None:
                        trade = (t0, cs[-1][0], entry, dr, dr * (cs[-1][4] - entry) / entry * 100)
            out[d][sym] = (rv, trade)
            tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close)) if prev_close else hi - lo
            trs.append(tr)
            or_vols.append(orv)
            prev_close = cs[-1][4]
    return out


def money(trades, leg):
    cash, open_, net_sum = CAPITAL, [], 0.0
    months = defaultdict(float)
    eq = peak = dd = 0.0
    n = 0
    for t0, t1, sym, e, d, ret in sorted(trades):
        keep = []
        for x in open_:
            if x[0] <= t0:
                cash += x[1] + x[2]
            else:
                keep.append(x)
        open_ = keep
        q = int(min(leg, cash) // e)
        if q <= 0:
            continue
        ex = e * (1 + d * ret / 100)
        sell, buy = (ex, e) if d > 0 else (e, ex)
        net = q * (ex - e) * d - itemized_round_trip_cost(q, sell, buy) - q * e * SLIP / 100
        cash -= q * e
        open_.append((t1, q * e, net + q * e * SLIP / 100, sym))
        net_sum += net
        eq += net
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        months[(t0 + 19800) // (86400 * 30)] += net
        n += 1
    return n, net_sum, dd


print(f"{'variant':44s} {'leg':>6s} | {'Y1 tr':>5s} {'Y1 net':>9s} {'Y1 dd':>8s} | {'Y2 tr':>5s} {'Y2 net':>9s} {'Y2 dd':>8s} | win  avg%")
for or_min in (5, 15):
    for stop_k in (0.10, 0.20):
        S = setups(or_min, stop_k)
        for rv_min, top_k in ((0, 13), (1.5, 13), (2.0, 13), (1.5, 2), (2.0, 2), (3.0, 13)):
            for universe, uname in ((set(SYMBOLS), "13"),):
                picks = []
                for d, rows in S.items():
                    cand = [(rv, sym, t) for sym, (rv, t) in rows.items()
                            if sym in universe and rv is not None and rv >= rv_min and t]
                    cand.sort(reverse=True)
                    for rv, sym, t in cand[:top_k]:
                        picks.append((t[0], t[1], sym, t[2], t[3], t[4]))
                if not picks:
                    continue
                rets = [p[5] for p in picks]
                for leg in (250_000, 125_000):
                    y1 = money([p for p in picks if p[0] < Y2_START], leg)
                    y2 = money([p for p in picks if p[0] >= Y2_START], leg)
                    name = f"OR {or_min}m, stop {stop_k}xATRd, RV>={rv_min}, top {top_k}"
                    print(f"{name:44s} {leg / 1e5:5.2f}L | {y1[0]:5d} {y1[1]:9,.0f} {y1[2]:8,.0f} | "
                          f"{y2[0]:5d} {y2[1]:9,.0f} {y2[2]:8,.0f} | {sum(r > 0 for r in rets) / len(rets):.0%} "
                          f"{sum(rets) / len(rets):+.3f}", flush=True)
