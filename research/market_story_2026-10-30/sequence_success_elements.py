"""Which story elements were present when LL->HH->HL->HH (and the mirror) worked?
PRELIMINARY: 13 stocks x 2 years, 5 min.

1. Every sequence (swings 5 candles each side, alternating, inside one day);
   at the candle the 4th swing became known: the story snapshot, oriented to the
   trend (e.g. turn up + price above VWAP -> price_vs_vwap=with). Success = the
   next 12 candles closed in the trend's direction.
2. Per element: how many sequences had it, success rate with / without, and the
   same split for year 1 and year 2 (an element counts only if it helps in both).
3. Pairs of the elements that helped.
4. Overall data: on all candles 09:30-14:45 (both directions, oriented the
   same way), how common each helpful element is and its next-12 success rate,
   i.e. does it help everywhere or only after the sequence."""
import pickle
from collections import defaultdict
from itertools import combinations

from mcommon import HERE, SYMBOLS, Y2_START, candles, day, minute_of_day
from story.swings import SwingTracker

N = 12
SN = pickle.load(open(f"{HERE}/snaps_5min.pkl", "rb"))

PAIRS = {  # state for the up side -> state for the down side
    "above": "below", "rising": "falling", "uptrend": "downtrend", "near_high": "near_low",
    "above_high": "below_low", "upper_half": "lower_half", "above_upper": "below_lower",
    "k_over_d": "k_under_d", "60_70": "30_40", "70p": "lt30",
}
WITH = {**{u: "with" for u in PAIRS}, **{d: "against" for d in PAIRS.values()}}


def orient(subject, state, d):
    """state relative to direction d (+1 up / -1 down)."""
    if state.startswith(("bull_", "bear_")):
        side, rest = state.split("_", 1)
        return ("with" if (side == "bull") == (d > 0) else "against") + "_" + rest
    if state.endswith(("_plus", "_minus")):
        zone, side = state.rsplit("_", 1)
        return zone + "_" + ("with" if (side == "plus") == (d > 0) else "against")
    if state in WITH:
        w = WITH[state]
        if d < 0:
            w = "against" if w == "with" else "with"
        return w
    return state  # not directional: expanding, constant, testing, high volume...


def elements(snap, d):
    out = set()
    for (subject, kind), val in snap.items():
        if kind == "state":
            out.add(f"{subject}={orient(subject, val, d)}")
    return out


def fwd(cs, k, d):
    dk = day(cs[k][0])
    e = k
    while e + 1 < len(cs) and e + 1 <= k + N and day(cs[e + 1][0]) == dk:
        e += 1
    return None if e == k else d * (cs[e][4] - cs[k][4]) / cs[k][4] * 100


# 1. sequences
seqs = []  # (elements, success, year, move)
for sym in SYMBOLS:
    cs = candles(sym, "5min")
    sw = SwingTracker(mode="fixed", lookback=5, alternate=True)
    seq = []
    for k, c in enumerate(cs):
        for s in sw.update({"ts": c[0], "high": c[2], "low": c[3]}):
            if s.replaced and seq:
                seq.pop()
            seq.append((s.label, s.index, s.confirm_index))
            if len(seq) < 4:
                continue
            labels = tuple(x[0] for x in seq[-4:])
            d = 1 if labels == ("LL", "HH", "HL", "HH") else -1 if labels == ("HH", "LL", "LH", "LL") else 0
            if not d or day(cs[seq[-4][1]][0]) != day(cs[seq[-1][1]][0]):
                continue
            k4 = seq[-1][2]
            f = fwd(cs, k4, d)
            if f is None:
                continue
            seqs.append((elements(SN[sym][k4], d), f > 0, "Y2" if cs[k4][0] >= Y2_START else "Y1", f))

n = len(seqs)
base = sum(s[1] for s in seqs) / n
print(f"== {n} sequences, success (next 12 candles in the trend's direction) {base:.1%}")

# 2. per element
stats = []
for el in sorted({e for s in seqs for e in s[0]}):
    has = [s for s in seqs if el in s[0]]
    if len(has) < 40:
        continue
    rate = sum(s[1] for s in has) / len(has)
    rest = [s for s in seqs if el not in s[0]]
    rrate = sum(s[1] for s in rest) / len(rest) if rest else 0
    yr = {}
    for y in ("Y1", "Y2"):
        h = [s for s in has if s[2] == y]
        o = [s for s in seqs if s[2] == y and el not in s[0]]
        yr[y] = (sum(s[1] for s in h) / len(h) if h else 0, sum(s[1] for s in o) / len(o) if o else 0, len(h))
    both = all(yr[y][0] > yr[y][1] + 0.03 for y in yr)
    stats.append((rate - rrate, el, len(has), rate, rrate, yr, both, sum(s[3] for s in has) / len(has)))
stats.sort(reverse=True)
print(f"\n{'element (oriented to the trend)':34s} {'in':>5s} {'success':>8s} {'without':>8s} {'avg move':>8s} | "
      f"{'Y1 with/without':>16s} | {'Y2 with/without':>16s} | both years")
for part, rows_ in (("best", stats[:18]), ("worst", stats[-8:])):
    if part == "worst":
        print("   ... (elements that hurt most)")
    for diff, el, cnt, rate, rrate, yr, both, mv in rows_:
        print(f"{el:34s} {cnt:5d} {rate:8.1%} {rrate:8.1%} {mv:+8.3f} | "
              f"{yr['Y1'][0]:6.1%}/{yr['Y1'][1]:6.1%} n{yr['Y1'][2]:<3d} | {yr['Y2'][0]:6.1%}/{yr['Y2'][1]:6.1%} "
              f"n{yr['Y2'][2]:<3d} | {'YES' if both else ''}")

good = [el for diff, el, cnt, rate, rrate, yr, both, mv in stats if both]
print(f"\nelements that helped in BOTH years by 3+ points: {len(good)}")

# 3. pairs of helpful elements
print("\n== pairs of those elements")
prs = []
for a, b in combinations(good, 2):
    has = [s for s in seqs if a in s[0] and b in s[0]]
    if len(has) < 30:
        continue
    yr = {y: [s for s in has if s[2] == y] for y in ("Y1", "Y2")}
    if not all(yr.values()):
        continue
    r = {y: sum(s[1] for s in v) / len(v) for y, v in yr.items()}
    prs.append((min(r.values()), a, b, len(has), sum(s[1] for s in has) / len(has), r))
prs.sort(reverse=True)
for mn, a, b, cnt, rate, r in prs[:10]:
    print(f"  {a} + {b}: n={cnt}, success {rate:.1%} (Y1 {r['Y1']:.1%}, Y2 {r['Y2']:.1%})")

# 4. the same elements on all candles (both directions)
print("\n== on ALL candles 09:30-14:45 (each candle counted for up and for down)")
allc = defaultdict(lambda: [0, 0, 0.0])
total = [0, 0]
for sym in SYMBOLS:
    cs = candles(sym, "5min")
    for k in range(0, len(cs), 2):  # every 2nd candle keeps it quick
        if not (570 <= minute_of_day(cs[k][0]) < 885):
            continue
        for d in (1, -1):
            f = fwd(cs, k, d)
            if f is None:
                continue
            total[0] += 1
            total[1] += f > 0
            els = elements(SN[sym][k], d)
            for el in good:
                if el in els:
                    c = allc[el]
                    c[0] += 1
                    c[1] += f > 0
                    c[2] += f
            if prs:
                for mn, a, b, *_ in prs[:5]:
                    if a in els and b in els:
                        c = allc[a + " + " + b]
                        c[0] += 1
                        c[1] += f > 0
                        c[2] += f
print(f"  all candle-directions: {total[0]}, success {total[1] / total[0]:.1%}")
for el, (cnt, ok, mv) in sorted(allc.items(), key=lambda x: -x[1][1] / max(1, x[1][0])):
    print(f"  {el:60s} present {cnt / total[0]:6.1%} of the time, success {ok / cnt:.1%}, avg {mv / cnt:+.3f}%")
