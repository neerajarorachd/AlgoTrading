"""Line tests as episodes (user, 2026-10-30): first touch after an approach
starts a test, closes inside the zone keep it going, a close out of the zone
ends it as a bounce (back to the side it came from) or a break (through).
13 stocks x 2 years, 5 min. Per setting: tests per stock-day, length, bounce
share, next 12 candles after the exit in the exit's direction, and the same
split by the EMA50 trend (exit with the trend vs against it)."""
from collections import defaultdict

from mcommon import SYMBOLS, candles, rows_from_candles
from story.indicator_rows import ist_date
from story.point_events import LINES, LineTests

N = 12
CONFIGS = {
    "fixed 0.05%, arm 3x": dict(zone_pct=0.05, arm_mult=3),
    "fixed 0.10%, arm 3x": dict(zone_pct=0.10, arm_mult=3),
    "fixed 0.10%, arm 5x": dict(zone_pct=0.10, arm_mult=5),
    "0.10 x ATR, arm 3x": dict(zone_atr=0.10, arm_mult=3),
    "0.20 x ATR, arm 3x": dict(zone_atr=0.20, arm_mult=3),
}
data = {s: rows_from_candles(candles(s, "5min")) for s in SYMBOLS}
n_days = sum(len({ist_date(r["ts"]) for r in rows}) for rows in data.values())


def fwd(rows, j, d):
    dj = ist_date(rows[j]["ts"])
    k = j
    while k + 1 < len(rows) and k + 1 <= j + N and ist_date(rows[k + 1]["ts"]) == dj:
        k += 1
    if k == j:
        return None
    return d * (rows[k]["close"] - rows[j]["close"]) / rows[j]["close"] * 100


def trend(rows, j):
    """+1 / -1 when price is on the side of a sloping EMA50 (10 candles), else 0."""
    e, e0 = rows[j].get("ema50"), rows[j - 10].get("ema50") if j >= 10 else None
    if e is None or e0 is None:
        return 0
    c = rows[j]["close"]
    if c > e and e > e0:
        return 1
    if c < e and e < e0:
        return -1
    return 0


def stat(xs):
    if not xs:
        return "n=    0"
    return (f"n={len(xs):6d} next12 {sum(x for x in xs) / len(xs):+.3f}% "
            f"right {sum(x > 0 for x in xs) / len(xs):.1%}")


for label, kw in CONFIGS.items():
    starts = 0
    by = defaultdict(list)
    lens = []
    zones = defaultdict(list)
    for sym, rows in data.items():
        lt = LineTests(**kw)
        for j, r in enumerate(rows):
            if j % 25 == 0:
                zones[sym].append(lt.zone_for(r))
            for _, name, inten in lt.update(r, ist_date(r["ts"])):
                if "_test_from_" in name:
                    starts += 1
                    continue
                kind = "bounce" if "_bounce_" in name else "break"
                d = 1 if name.endswith("_up") else -1
                f = fwd(rows, j, d)
                if f is None:
                    continue
                lens.append(inten)
                t = trend(rows, j)
                ctx = "with trend" if t == d else "against trend" if t == -d else "no trend"
                by[(kind, "all")].append(f)
                by[(kind, ctx)].append(f)
                if inten >= 3:
                    by[(kind, "3+ candles")].append(f)
    lens.sort()
    nb, nk = len(by[("bounce", "all")]), len(by[("break", "all")])
    print(f"\n== {label}: {starts / n_days:.1f} tests per stock-day (7 lines), median {lens[len(lens) // 2]:.0f} "
          f"candles, 1-candle {sum(x == 1 for x in lens) / len(lens):.0%}, bounce share {nb / (nb + nk):.0%}")
    if "zone_atr" in kw:
        med = {s: sorted(v)[len(v) // 2] for s, v in zones.items()}
        lo, hi = min(med, key=med.get), max(med, key=med.get)
        print(f"   zone per stock (median): {lo} {med[lo]:.3f}% ... {hi} {med[hi]:.3f}%")
    for kind in ("bounce", "break"):
        for ctx in ("all", "with trend", "against trend", "no trend", "3+ candles"):
            print(f"   {kind:6s} {ctx:13s} {stat(by[(kind, ctx)])}")
