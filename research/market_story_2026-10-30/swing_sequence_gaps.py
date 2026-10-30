"""Leg sizes in LL -> HH -> HL -> HH (turn to an uptrend) and the mirror
HH -> LL -> LH -> LL (turn to a downtrend). 13 stocks x 2 years, 5 min,
swings = 5 candles each side, alternating high / low (a second high in a row
replaces the first only if higher). PRELIMINARY (13 stocks).

For every match: leg 1 (LL -> HH), leg 2 (HH -> HL pullback), leg 3 (HL -> new
HH), as % of the starting swing's price; pullback ratio = leg 2 / leg 1; and
the next 12 candles after the last swing is KNOWN (5 candles late)."""
from collections import defaultdict

from mcommon import SYMBOLS, candles, day
from story.swings import SwingTracker

N = 12


def pct(a, b):
    return (b - a) / a * 100


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def row(name, xs, unit="%"):
    if not xs:
        return f"  {name:28s} n=0"
    return (f"  {name:28s} n={len(xs):5d}  median {q(xs, .5):6.2f}{unit}  middle half {q(xs, .25):5.2f}-"
            f"{q(xs, .75):5.2f}{unit}  10-90% {q(xs, .1):5.2f}-{q(xs, .9):5.2f}{unit}")


res = {"up": defaultdict(list), "down": defaultdict(list)}
per_stock = defaultdict(lambda: defaultdict(list))
for sym in SYMBOLS:
    cs = candles(sym, "5min")
    sw = SwingTracker(mode="fixed", lookback=5, alternate=True)
    seq = []  # (label, price, index, confirm_index)
    for k, c in enumerate(cs):
        for s in sw.update({"ts": c[0], "high": c[2], "low": c[3]}):
            if s.replaced and seq:
                seq.pop()
            seq.append((s.label, s.price, s.index, s.confirm_index))
            if len(seq) < 4:
                continue
            (l1, p1, i1, _), (l2, p2, _, _), (l3, p3, _, _), (l4, p4, i4, k4) = seq[-4:]
            if (l1, l2, l3, l4) == ("LL", "HH", "HL", "HH"):
                side, d = "up", 1
            elif (l1, l2, l3, l4) == ("HH", "LL", "LH", "LL"):
                side, d = "down", -1
            else:
                continue
            if day(cs[i1][0]) != day(cs[i4][0]):
                continue  # keep the whole sequence inside one day
            a, b, c3 = abs(pct(p1, p2)), abs(pct(p2, p3)), abs(pct(p3, p4))
            r = res[side]
            r["leg1"].append(a)
            r["leg2"].append(b)
            r["leg3"].append(c3)
            r["ratio"].append(b / a if a else 0)
            r["span"].append(i4 - i1)
            per_stock[sym][side].append((a, b, c3))
            # what came after the 4th swing became known
            dk = day(cs[k4][0])
            e = k4
            while e + 1 < len(cs) and e + 1 <= k4 + N and day(cs[e + 1][0]) == dk:
                e += 1
            if e > k4:
                r["next"].append(d * pct(cs[k4][4], cs[e][4]))
                r["gone"].append(d * pct(p4, cs[k4][4]) * -1 if d > 0 else pct(p4, cs[k4][4]))

for side, title in (("up", "LL -> HH -> HL -> HH (turn up)"), ("down", "HH -> LL -> LH -> LL (turn down)")):
    r = res[side]
    print(f"\n== {title}: {len(r['leg1'])} sequences within one day")
    print(row("leg 1  LL->HH / HH->LL", r["leg1"]))
    print(row("leg 2  pullback", r["leg2"]))
    print(row("leg 3  new HH / new LL", r["leg3"]))
    print(row("pullback ratio (leg2/leg1)", r["ratio"], "x"))
    print(row("candles from 1st to 4th swing", r["span"], " "))
    nx = r["next"]
    print(f"  next 12 candles after it is known (5 candles late): avg {sum(nx) / len(nx):+.3f}%, "
          f"in the trend's direction {sum(x > 0 for x in nx) / len(nx):.1%}")

print("\n== median legs per stock, turn up (leg1 / leg2 / leg3 %) | turn down")
for sym in SYMBOLS:
    cells = []
    for side in ("up", "down"):
        v = per_stock[sym][side]
        cells.append(f"n={len(v):3d} " + " / ".join(f"{q([x[i] for x in v], .5):.2f}" for i in range(3)) if v else "n=0")
    print(f"  {sym:11s} {cells[0]:28s} | {cells[1]}")
