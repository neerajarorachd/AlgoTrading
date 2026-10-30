"""Enter at the first HL (user, 2026-10-30): after LL -> HH, the first HL is the
entry, aiming for the second HH. Mirror: after HH -> LL, the first LH is a short
entry aiming for the second LL. PRELIMINARY: 13 stocks x 2 years, 5 min.

Entry: close of the candle where the HL is confirmed (09:30-14:45), only if
that close is still between the stop and the target.
Stop: 0.05% beyond the HL price.
Targets: back to the first HH ("prior HH"), HL + 0.7 x leg 1, HL + 1.0 x leg 1
(leg 1 = LL -> HH); else exit at day end.
Swing confirmation: fixed 5 / fixed 3 candles each side (alternating), or
ZigZag 0.3% / 0.5% (confirmed once price moves that far off the HL).
Money: Rs 2.5L per trade, itemized costs + 0.05% slippage; Rs per year = /2.
Early-reversal filter: the trade's story at entry has prev_day=against or
rsi=against or vwap_slope=against or adx=20_30_against (oriented)."""
import sys
from collections import defaultdict

sys.path.insert(0, "D:/AI Trading/AlgoTrading-signals/backend/scripts")
from order_backtest import itemized_round_trip_cost  # noqa: E402

from mcommon import SYMBOLS, Y2_START, candles, day, minute_of_day  # noqa: E402
from sequence_success_elements import SN, elements  # noqa: E402
from story.swings import SwingTracker  # noqa: E402

LEG, SLIP, BUF = 250_000, 0.05, 0.05
EARLY = {"prev_day=against", "rsi=against", "vwap_slope=against", "adx=20_30_against"}
MODES = {
    "fixed 5 (today)": dict(mode="fixed", lookback=5, alternate=True),
    "fixed 3": dict(mode="fixed", lookback=3, alternate=True),
}
TARGETS = {"prior HH": None, "0.7 x leg 1": 0.7, "1.0 x leg 1": 1.0}


def run_trade(cs, k, d, entry, stop, tgt):
    dk = day(cs[k][0])
    j = k
    while j + 1 < len(cs) and day(cs[j + 1][0]) == dk:
        j += 1
        ts, o, h, l, c, v = cs[j]
        if (d > 0 and l <= stop) or (d < 0 and h >= stop):
            return d * ((min(o, stop) if d > 0 else max(o, stop)) - entry) / entry * 100, "stop"
        if (d > 0 and h >= tgt) or (d < 0 and l <= tgt):
            return d * ((max(o, tgt) if d > 0 else min(o, tgt)) - entry) / entry * 100, "target"
    if j == k:
        return None
    return d * (cs[j][4] - entry) / entry * 100, "day end"


def net_rs(entry, ret, d):
    q = int(LEG // entry)
    ex = entry * (1 + d * ret / 100)
    sell, buy = (ex, entry) if d > 0 else (entry, ex)
    return q * (ex - entry) * d - itemized_round_trip_cost(q, sell, buy) - q * entry * SLIP / 100


res = defaultdict(list)  # (mode, target, filter) -> [(year, ret, net, why)]
for mname, kw in MODES.items():
    for sym in SYMBOLS:
        cs = candles(sym, "5min")
        sw = SwingTracker(**kw)
        seq = []
        busy_until = -1
        for k, c in enumerate(cs):
            for s in sw.update({"ts": c[0], "high": c[2], "low": c[3]}):
                if s.replaced and seq:
                    seq.pop()
                seq.append((s.label, s.price, s.index))
                if len(seq) < 3 or k <= busy_until:
                    continue
                labels = tuple(x[0] for x in seq[-3:])
                d = 1 if labels == ("LL", "HH", "HL") else -1 if labels == ("HH", "LL", "LH") else 0
                if not d or not (570 <= minute_of_day(cs[k][0]) < 885):
                    continue
                (_, p1, i1), (_, p2, _), (_, p3, _) = seq[-3:]
                if day(cs[i1][0]) != day(cs[k][0]):
                    continue  # whole setup inside today
                entry = cs[k][4]
                stop = p3 * (1 - d * BUF / 100)
                leg1 = abs(p2 - p1)
                els = elements(SN[sym][k], d)
                early = bool(els & EARLY)
                year = "Y2" if cs[k][0] >= Y2_START else "Y1"
                last = None
                for tname, mult in TARGETS.items():
                    tgt = p2 if mult is None else p3 + d * mult * leg1
                    if not (d * (entry - stop) > 0 and d * (tgt - entry) > 0):
                        continue  # already past the target or through the stop
                    o = run_trade(cs, k, d, entry, stop, tgt)
                    if not o:
                        continue
                    ret, why = o
                    rec = (year, ret, net_rs(entry, ret, d), why, d * (tgt - entry) / entry * 100,
                           d * (entry - stop) / entry * 100)
                    pull = abs(p2 - p3) / p2 * 100   # HH -> HL gap
                    rise = abs(p2 - p1) / p1 * 100   # LL -> HH gap
                    if pull >= 1.0:
                        for rmin in (1.0, 1.5, 2.0):
                            if rise > rmin:
                                res[(mname, tname, f"rise>{rmin:g}%,dip>=1%")].append(rec)
                                if early:
                                    res[(mname, tname, f"rise>{rmin:g}%,dip>=1%+early")].append(rec)
                    last = k
                if last is not None:
                    busy_until = last  # one setup at a time per stock

print(f"{'confirmation':17s} {'target':12s} {'filter':26s} {'tr/yr':>6s} {'win':>5s} {'risk%':>6s} {'reward%':>7s} "
      f"{'gross%':>7s} {'net%':>7s} {'Rs/yr':>9s} {'Y1 Rs':>9s} {'Y2 Rs':>9s}  exits")
for (m, t, f), rs in res.items():
    n = len(rs)
    nets = [r[2] for r in rs]
    why = defaultdict(int)
    for r in rs:
        why[r[3]] += 1
    y1 = sum(r[2] for r in rs if r[0] == "Y1")
    y2 = sum(r[2] for r in rs if r[0] == "Y2")
    print(f"{m:17s} {t:12s} {f:26s} {n / 2:6.0f} {sum(r[1] > 0 for r in rs) / n:5.0%} "
          f"{sum(r[5] for r in rs) / n:6.2f} {sum(r[4] for r in rs) / n:7.2f} {sum(r[1] for r in rs) / n:+7.3f} "
          f"{sum(nets) / n / LEG * 100:+7.3f} {sum(nets) / 2:9,.0f} {y1:9,.0f} {y2:9,.0f}  "
          + " ".join(f"{k} {v / n:.0%}" for k, v in sorted(why.items())), flush=True)
