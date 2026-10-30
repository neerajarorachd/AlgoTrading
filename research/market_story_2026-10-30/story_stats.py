"""Per subject: runs per day, typical length, flicker (runs of 1-2 candles),
state shares; anti-flicker hold 1 vs 2 vs 3 candles; value spread for the
threshold choices. 5 min unless stated."""
import pickle
import statistics
from collections import Counter, defaultdict

from mcommon import HERE, SYMBOLS, candles, day, rows_from_candles, run_tracker
from story.subjects import Ctx, DEFAULTS, default_subjects

S = pickle.load(open(f"{HERE}/stories_5min.pkl", "rb"))
n_days = sum(len({day(t) for t in S[s]["ts"]}) for s in SYMBOLS)

print("== 5 min, hold 2 candles (default), all 13 stocks")
print(f"{'subject':16s} {'runs/day':>8s} {'median':>6s} {'<=2c':>6s}  states (share of candles)")
per = defaultdict(list)
for s in SYMBOLS:
    for r in S[s]["runs"]:
        per[r["subject"]].append(r)
for subj in [x.name for x in default_subjects()]:
    rs = per[subj]
    if not rs:
        print(f"{subj:16s} no runs"); continue
    lens = [r["candles"] for r in rs]
    share = Counter()
    for r in rs:
        share[r["state"]] += r["candles"]
    tot = sum(share.values())
    st = " ".join(f"{k}={v / tot:.0%}" for k, v in share.most_common())
    print(f"{subj:16s} {len(rs) / n_days:8.1f} {statistics.median(lens):6.0f} "
          f"{sum(l <= 2 for l in lens) / len(lens):6.0%}  {st}")

print("\n== hold 1 / 2 / 3 candles: total runs per stock-day (RELIANCE, HINDCOPPER, TCS)")
for hold in (1, 2, 3):
    tot = days = 0
    short = 0
    for sym in ("RELIANCE", "HINDCOPPER", "TCS"):
        cs = candles(sym, "5min")
        runs, _, _ = run_tracker(rows_from_candles(cs), settings={"confirm_candles": hold})
        tot += len(runs)
        short += sum(r.candles <= 2 for r in runs)
        days += len({day(c[0]) for c in cs})
    print(f"hold={hold}: {tot / days:6.1f} runs/day, {short / tot:5.1%} of runs are 1-2 candles")

print("\n== value spread per candle (|value|, percentiles 25/50/75/90), RELIANCE + HINDCOPPER + TCS")
vals = defaultdict(list)
subs = default_subjects()
for sym in ("RELIANCE", "HINDCOPPER", "TCS"):
    ctx = Ctx(dict(DEFAULTS))
    for s in subs:
        s.settings = ctx.settings
    for r in rows_from_candles(candles(sym, "5min")):
        ctx.rows.append(r)
        if len(ctx.rows) > 60:
            del ctx.rows[:-60]
        for s in subs:
            if s.name in ("vwap_slope", "ema50_slope", "ma50_slope", "price_vs_vwap", "price_vs_ema50",
                          "vwap_gap", "bb_width"):
                o = s.observe(ctx)
                if o:
                    vals[s.name].append(abs(o[1]) if s.name != "vwap_gap" else o[2])
for k, v in vals.items():
    v.sort()
    q = [v[int(len(v) * p)] for p in (0.25, 0.5, 0.75, 0.9)]
    unit = "rel %" if k == "vwap_gap" else "% (bb: width %)" if k == "bb_width" else "%"
    print(f"{k:16s} " + " ".join(f"{x:7.3f}" for x in q) + f"   {unit}")
print("(vwap_gap column = relative change of the gap over 5 candles; slopes = % move over 5 candles)")
