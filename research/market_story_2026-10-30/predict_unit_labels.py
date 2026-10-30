"""Year-1 evidence index for the setup prediction, importable (predict_unit.py
runs its whole study on import). Same question and filters as predict_unit:
up 0.5% before down 0.25% within 12 candles (and the mirror), 09:30-14:45."""
import pickle

from mcommon import HERE, SYMBOLS, Y2_START, candles, day, minute_of_day
from story.setup import EvidenceIndex

X_UP, Y_DN, N = 0.5, 0.25, 12


def labels(cs):
    up, dn = [0] * len(cs), [0] * len(cs)
    for i, (ts, o, h, l, c, v) in enumerate(cs):
        d = day(ts)
        tu, su = c * (1 + X_UP / 100), c * (1 - Y_DN / 100)
        td, sd = c * (1 - X_UP / 100), c * (1 + Y_DN / 100)
        ru = rd = None
        for j in range(i + 1, min(i + 1 + N, len(cs))):
            if day(cs[j][0]) != d:
                break
            hj, lj = cs[j][2], cs[j][3]
            if ru is None:
                ru = 0 if lj <= su else 1 if hj >= tu else None
            if rd is None:
                rd = 0 if hj >= sd else 1 if lj <= td else None
            if ru is not None and rd is not None:
                break
        up[i], dn[i] = ru or 0, rd or 0
    return up, dn


def train_index(min_cases=300):
    SN = pickle.load(open(f"{HERE}/snaps_5min.pkl", "rb"))
    snaps, ys = [], {"up": [], "dn": []}
    for sym in SYMBOLS:
        cs = candles(sym, "5min")
        up, dn = labels(cs)
        for i, s in enumerate(SN[sym]):
            ts = cs[i][0]
            if ts >= Y2_START or not (570 <= minute_of_day(ts) < 885) or ("macd", "state") not in s:
                continue
            snaps.append(s)
            ys["up"].append(up[i])
            ys["dn"].append(dn[i])
    out = {side: EvidenceIndex(min_cases=min_cases).fit(snaps, ys[side]) for side in ("up", "dn")}
    out["base"] = {side: sum(ys[side]) / len(ys[side]) for side in ("up", "dn")}
    return out
