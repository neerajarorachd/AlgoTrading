"""Prediction unit, walk-forward: learn on year 1, judge year 2 (5 min, 13 stocks).

Question per candle (09:30-14:45 entries): does price go up X% before down Y%
within N candles, same day (and the mirror for down)? Stop is checked first
when both happen in one candle.

Variants, all scored on year 2 only:
  base      the year-1 hit rate for everyone (no information)
  macd      MACD state alone (back-off level 0)
  backoff   full setup, back off to >= 30 cases, MACD locked
  free      same, but MACD may be dropped too
  gbm       gradient boosting on the same setup features
Caveat: consecutive candles overlap in what they predict, so n is inflated;
the comparison between variants is still like-for-like."""
import pickle
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

from mcommon import HERE, SYMBOLS, Y2_START, candles, day, minute_of_day
from story.setup import EvidenceIndex

X_UP, Y_DN, N = 0.5, 0.25, 12
SN = pickle.load(open(f"{HERE}/snaps_5min.pkl", "rb"))


def labels(cs):
    up, dn = [None] * len(cs), [None] * len(cs)
    for i, (ts, o, h, l, c, v) in enumerate(cs):
        d = day(ts)
        tu, su = c * (1 + X_UP / 100), c * (1 - Y_DN / 100)   # long: target / stop
        td, sd = c * (1 - X_UP / 100), c * (1 + Y_DN / 100)   # short
        ru = rd = None
        for j in range(i + 1, min(i + 1 + N, len(cs))):
            if day(cs[j][0]) != d:
                break
            hj, lj = cs[j][2], cs[j][3]
            if ru is None:
                if lj <= su: ru = 0
                elif hj >= tu: ru = 1
            if rd is None:
                if hj >= sd: rd = 0
                elif lj <= td: rd = 1
            if ru is not None and rd is not None:
                break
        up[i], dn[i] = (ru if ru is not None else 0), (rd if rd is not None else 0)
    return up, dn


tr, te = {"snap": [], "up": [], "dn": []}, {"snap": [], "up": [], "dn": [], "key": []}
for sym in SYMBOLS:
    cs = candles(sym, "5min")
    up, dn = labels(cs)
    for i, s in enumerate(SN[sym]):
        ts = cs[i][0]
        if not (570 <= minute_of_day(ts) < 885) or ("macd", "state") not in s:
            continue
        b = te if ts >= Y2_START else tr
        b["snap"].append(s); b["up"].append(up[i]); b["dn"].append(dn[i])
        if b is te:
            b["key"].append((sym, i))
print(f"train moments {len(tr['snap'])}, test moments {len(te['snap'])}")

ALL = ("all", "state")
results = {}
feat = sorted({c for s in tr["snap"] for c in s})
for side in ("up", "dn"):
    ytr, yte = np.array(tr[side]), np.array(te[side])
    base = ytr.mean()
    preds = {"base": np.full(len(yte), base)}
    covered = {}
    # macd alone + MACD-locked back-off
    for mc in (30, 100, 300):
        idx = EvidenceIndex(min_cases=mc).fit(tr["snap"], tr[side])
        pm, pb, ps, cov = [], [], [], []
        for s in te["snap"]:
            a = s[("macd", "state")]
            n0, w0 = idx.base.get(a, (0, 0))
            g = w0 / n0 if n0 else base
            pm.append(g)
            p = idx.predict(s)
            pb.append(p["prob"] if p else g)
            # shrunk toward the MACD group's rate, as if 30 extra average cases were added
            ps.append((p["prob"] * p["cases"] + 30 * g) / (p["cases"] + 30) if p else g)
            cov.append(p["level"] if p else -1)
        preds["macd"] = np.array(pm)
        preds[f"backoff{mc}"] = np.array(pb)
        preds[f"backoff{mc}s"] = np.array(ps)
        covered["backoff"] = np.array(cov)
    # MACD droppable: one constant anchor for everyone
    free_tr = [{**s, ALL: "x"} for s in tr["snap"]]
    fidx = EvidenceIndex(min_cases=30, anchor="all").fit(free_tr, tr[side])
    pf = []
    for s in te["snap"]:
        p = fidx.predict({**s, ALL: "x"})
        pf.append(p["prob"] if p else base)
    preds["free"] = np.array(pf)
    macd_rank = fidx.order["x"].index(("macd", "state")) + 1
    # gradient boosting
    def frame(snaps):
        return pd.DataFrame({f"{a}.{b}": [s.get((a, b), "na") for s in snaps] for a, b in feat}).astype("category")
    Xtr = frame(tr["snap"])
    Xte = frame(te["snap"])
    for col in Xte:
        Xte[col] = pd.Categorical(Xte[col], categories=Xtr[col].cat.categories)
    gbm = HistGradientBoostingClassifier(categorical_features="from_dtype", max_iter=300, learning_rate=0.05,
                                         max_leaf_nodes=31, min_samples_leaf=200, random_state=7)
    gbm.fit(Xtr, ytr)
    preds["gbm"] = gbm.predict_proba(Xte)[:, 1]

    print(f"\n== {side}: target {X_UP}% before stop {Y_DN}% within {N} candles. "
          f"Year-1 hit rate {base:.1%}, year-2 actual {yte.mean():.1%}")
    print(f"   (with MACD droppable, the learned order puts MACD state at #{macd_rank} of {len(fidx.order['x'])})")
    print(f"{'variant':12s} {'AUC':>6s} {'Brier skill':>11s} {'top 10% hit':>11s} {'top 5% hit':>10s}")
    for k, p in preds.items():
        auc = roc_auc_score(yte, p) if k != "base" else 0.5
        bs = brier_score_loss(yte, p)
        bs0 = brier_score_loss(yte, np.full(len(yte), base))
        o = np.argsort(-p, kind="stable")
        t10 = yte[o[: len(o) // 10]].mean()
        t5 = yte[o[: len(o) // 20]].mean()
        print(f"{k:12s} {auc:6.3f} {1 - bs / bs0:+11.3%} {t10:11.1%} {t5:10.1%}")
    lv = covered["backoff"]
    print(f"back-off level used (conditions kept besides MACD): median {int(np.median(lv[lv >= 0]))}, "
          f"no prediction {np.mean(lv < 0):.1%}")
    results[side] = {k: v for k, v in preds.items()}

base_rates = {side: float(np.mean(tr[side])) for side in ("up", "dn")}
keep = ("base", "macd", "backoff300s", "gbm")
pickle.dump({"key": te["key"], "base": base_rates,
             "preds": {side: {k: results[side][k].astype("float32") for k in keep} for side in results}},
            open(f"{HERE}/predict_y2.pkl", "wb"))
