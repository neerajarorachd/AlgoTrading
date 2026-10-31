"""Bounce zone per stock (user, 2026-10-30): verify 0.10 / 0.15 / 0.20% for the
bigger / more volatile stocks, next to 0.05% and the ATR-sized zone.
5 min, 2 years, all 7 lines. Per stock and zone: typical 5-min ATR %, tests
per day, share of tests that whipsaw out in 1 candle, bounce share, how far
price went in the bounce's direction in the next 12 candles (average and
"held": share of bounces where price never closed back through the line in
those 12 candles)."""
from collections import defaultdict

from mcommon import SYMBOLS, candles, rows_from_candles
from story.indicator_rows import ist_date
from story.point_events import LINES, LineTests

N = 12
ZONES = [("0.05%", dict(zone_pct=0.05, zone_atr=None)), ("0.10%", dict(zone_pct=0.10, zone_atr=None)),
         ("0.15%", dict(zone_pct=0.15, zone_atr=None)), ("0.20%", dict(zone_pct=0.20, zone_atr=None)),
         ("0.3xATR", dict(zone_atr=0.30))]

print(f"{'stock':11s} {'ATR%':>5s} | " + " | ".join(f"{z:^31s}" for z, _ in ZONES))
print(f"{'':11s} {'':5s} | " + " | ".join(f"{'tests/d 1c% bnc% fwd% held':31s}" for _ in ZONES))
rows_by = {}
for sym in SYMBOLS:
    rows = rows_from_candles(candles(sym, "5min"))
    atrp = sorted(r["atr"] / r["close"] * 100 for r in rows if r.get("atr"))
    days = len({ist_date(r["ts"]) for r in rows})
    cells = []
    for _, kw in ZONES:
        lt = LineTests(arm_mult=3, **kw)
        starts = 0
        lens, kinds, fw, held = [], defaultdict(int), [], []
        for j, r in enumerate(rows):
            for _, name, inten in lt.update(r, ist_date(r["ts"])):
                if "_test_from_" in name:
                    starts += 1
                    continue
                kind = "bounce" if "_bounce_" in name else "break"
                kinds[kind] += 1
                lens.append(inten)
                if kind != "bounce":
                    continue
                line = next(l for l in LINES if name.startswith(l + "_"))
                d = 1 if name.endswith("_up") else -1
                dj = ist_date(r["ts"])
                k, ok = j, True
                while k + 1 < len(rows) and k + 1 <= j + N and ist_date(rows[k + 1]["ts"]) == dj:
                    k += 1
                    lv = rows[k].get(line)
                    if lv and d * (rows[k]["close"] - lv) < 0:
                        ok = False
                if k > j:
                    fw.append(d * (rows[k]["close"] - r["close"]) / r["close"] * 100)
                    held.append(ok)
        tot = kinds["bounce"] + kinds["break"]
        cells.append(f"{starts / days:5.1f} {sum(x == 1 for x in lens) / max(1, len(lens)):4.0%} "
                     f"{kinds['bounce'] / max(1, tot):4.0%} {sum(fw) / max(1, len(fw)):+.3f} "
                     f"{sum(held) / max(1, len(held)):4.0%}")
    print(f"{sym:11s} {atrp[len(atrp) // 2]:5.2f} | " + " | ".join(f"{c:31s}" for c in cells), flush=True)
