"""Swing confirmation and quality (5 min, 13 stocks, 2 years).

Modes: the engine's rule today (fixed 5 candles each side), with alternation
and a minimum leg, and ZigZag with several reversal sizes. For every labelled
swing, measured from the CONFIRMATION candle's close (what we could act on):
- lag: candles between the swing candle and its confirmation;
- gone: how far price already moved away from the swing price at confirmation;
- next 12 candles (same day) in the label's direction: HH/HL = up, LH/LL = down.
Then, for ZigZag 0.5%: HL / LH by swing ratio band and by previous leg size,
answering "what minimum gap and ratio make a good HL"."""
from collections import defaultdict

from mcommon import SYMBOLS, candles, day
from story.swings import SwingTracker

N = 12
MODES = {
    "fixed5 (today)": dict(mode="fixed", lookback=5),
    "fixed5 alternate": dict(mode="fixed", lookback=5, alternate=True),
    "fixed5 alt leg>=0.5%": dict(mode="fixed", lookback=5, alternate=True, min_leg_pct=0.5),
    "fixed5 alt leg>=1%": dict(mode="fixed", lookback=5, alternate=True, min_leg_pct=1.0),
    "fixed3 alt leg>=0.5%": dict(mode="fixed", lookback=3, alternate=True, min_leg_pct=0.5),
    "zigzag 0.3%": dict(mode="zigzag", reversal_pct=0.3),
    "zigzag 0.5%": dict(mode="zigzag", reversal_pct=0.5),
    "zigzag 0.8%": dict(mode="zigzag", reversal_pct=0.8),
    "zigzag 1.0%": dict(mode="zigzag", reversal_pct=1.0),
}
UP = {"HH": 1, "HL": 1, "LH": -1, "LL": -1}

data = {s: candles(s, "5min") for s in SYMBOLS}
n_days = sum(len({day(c[0]) for c in cs}) for cs in data.values())


def forward(cs, j, d):
    """% move in direction d from candle j's close to the close N candles
    later (or the day's last candle)."""
    dj = day(cs[j][0])
    k = j
    while k + 1 < len(cs) and k + 1 <= j + N and day(cs[k + 1][0]) == dj:
        k += 1
    if k == j:
        return None
    return d * (cs[k][4] - cs[j][4]) / cs[j][4] * 100


detail = None
print(f"{'mode':22s} {'swings/day':>10s} {'lag':>5s} {'gone%':>6s} {'labelled':>8s} "
      f"{'next12 avg%':>11s} {'right dir':>9s}")
for name, kw in MODES.items():
    tot = lag = gone = 0.0
    fw, nlab = [], 0
    rows_ratio = []
    for sym, cs in data.items():
        sw = SwingTracker(**kw)
        for j, c in enumerate(cs):
            for s in sw.update({"ts": c[0], "high": c[2], "low": c[3]}):
                tot += 1
                lag += s.lag
                gone += abs(c[4] - s.price) / s.price * 100
                if s.label in UP:
                    f = forward(cs, j, UP[s.label])
                    if f is not None:
                        fw.append(f)
                        nlab += 1
                        rows_ratio.append((s.label, s.ratio, s.leg_pct, f))
    print(f"{name:22s} {tot / n_days:10.1f} {lag / tot:5.1f} {gone / tot:6.2f} {nlab:8d} "
          f"{sum(fw) / len(fw):+11.3f} {sum(f > 0 for f in fw) / len(fw):9.1%}")
    if name == "zigzag 0.5%":
        detail = rows_ratio

print("\n== zigzag 0.5%: after an HL (expect up) / LH (expect down), by swing ratio "
      "(pullback / previous leg)")
bands = [(0, 0.382), (0.382, 0.618), (0.618, 1.0), (1.0, 99)]
for lab in ("HL", "LH"):
    for lo, hi in bands:
        f = [x[3] for x in detail if x[0] == lab and x[1] is not None and lo <= x[1] < hi]
        if f:
            print(f"  {lab} ratio {lo:.3f}-{hi:<5} n={len(f):5d} next12 avg {sum(f) / len(f):+.3f}%  "
                  f"right dir {sum(v > 0 for v in f) / len(f):.1%}")

print("\n== zigzag 0.5%: after an HL / LH, by the previous leg's size (the move it pulls back from)")
legs = [(0, 0.75), (0.75, 1.0), (1.0, 1.5), (1.5, 2.5), (2.5, 99)]
for lab in ("HL", "LH"):
    for lo, hi in legs:
        f = [x[3] for x in detail if x[0] == lab and x[1] is not None and x[2]
             and lo <= abs(x[2]) / x[1] < hi]
        if f:
            print(f"  {lab} prev leg {lo}-{hi}% n={len(f):5d} next12 avg {sum(f) / len(f):+.3f}%  "
                  f"right dir {sum(v > 0 for v in f) / len(f):.1%}")
