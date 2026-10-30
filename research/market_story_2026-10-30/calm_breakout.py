"""Calm then move (user, 2026-10-30): BB width constant and the VWAP-price gap
constant together, then both start expanding in one direction. PRELIMINARY:
13 stocks only (the rule is 15-30 stocks x 2 years before deciding).

Event at candle j: bb_width just turned expanding and vwap_gap is expanding
(both runs started within the last 3 candles), and before that both had been
constant for at least `calm` candles. Direction: price above VWAP = up.
Measured: next 12 candles in that direction; split by the EMA50 trend; and
the same next-12 for all candles (baseline)."""
import pickle
from collections import defaultdict

from mcommon import HERE, SYMBOLS, candles, day, minute_of_day

S = pickle.load(open(f"{HERE}/stories_5min.pkl", "rb"))
N = 12


def fwd(cs, j, d):
    dj = day(cs[j][0])
    k = j
    while k + 1 < len(cs) and k + 1 <= j + N and day(cs[k + 1][0]) == dj:
        k += 1
    return None if k == j else d * (cs[k][4] - cs[j][4]) / cs[j][4] * 100


def show(label, xs):
    if not xs:
        print(f"  {label:34s} n=0"); return
    print(f"  {label:34s} n={len(xs):5d}  next12 avg {sum(xs) / len(xs):+.3f}%  "
          f"right dir {sum(x > 0 for x in xs) / len(xs):.1%}  avg |move| {sum(abs(x) for x in xs) / len(xs):.3f}%")


for calm in (4, 6, 10):
    res = defaultdict(list)
    base_abs = []
    per_day = 0
    days = 0
    for sym in SYMBOLS:
        cs = candles(sym, "5min")
        days += len({day(c[0]) for c in cs})
        runs = S[sym]["runs"]
        by = defaultdict(list)
        for r in runs:
            if r["subject"] in ("bb_width", "vwap_gap", "price_vs_vwap"):
                by[r["subject"]].append(r)
        # index: for each subject, which run covers candle j, and the run before it
        cover = {}
        for subj, rs in by.items():
            arr = [None] * len(cs)
            for k, r in enumerate(rs):
                for j in range(r["start_index"], r["end_index"] + 1):
                    arr[j] = k
            cover[subj] = arr
        fired_day = set()
        for j in range(len(cs)):
            if not (570 <= minute_of_day(cs[j][0]) < 885):
                continue
            kb, kg, kp = cover["bb_width"][j], cover["vwap_gap"][j], cover["price_vs_vwap"][j]
            if kb is None or kg is None or kp is None or kb == 0 or kg == 0:
                continue
            rb, rg = by["bb_width"][kb], by["vwap_gap"][kg]
            pb, pg = by["bb_width"][kb - 1], by["vwap_gap"][kg - 1]
            if not (rb["state"] == "expanding" and rg["state"] == "expanding"):
                continue
            if max(j - rb["start_index"], j - rg["start_index"]) > 2:
                continue  # both turned within the last 3 candles
            if not (pb["state"] == "constant" and pg["state"] == "constant"
                    and pb["candles"] >= calm and pg["candles"] >= calm):
                continue
            side = by["price_vs_vwap"][kp]["state"]
            if side not in ("above", "below"):
                continue
            if (sym, day(cs[j][0]), rb["start_index"]) in fired_day:
                continue  # one event per breakout
            fired_day.add((sym, day(cs[j][0]), rb["start_index"]))
            d = 1 if side == "above" else -1
            f = fwd(cs, j, d)
            if f is None:
                continue
            res["all"].append(f)
            # EMA50 trend from the 5-min closes (50-candle EMA, slope over 10)
        for j in range(0, len(cs), 7):
            if 570 <= minute_of_day(cs[j][0]) < 885:
                f = fwd(cs, j, 1)
                if f is not None:
                    base_abs.append(abs(f))
    print(f"\n== calm >= {calm} candles for both BB width and VWAP gap, then both expanding "
          f"({len(res['all']) / days:.2f} events per stock-day)")
    show("calm-breakout, in price's direction", res["all"])
    print(f"  baseline: any candle, avg |move| over next 12 = {sum(base_abs) / len(base_abs):.3f}%")
