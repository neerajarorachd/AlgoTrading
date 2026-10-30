"""Data for the trade chart page: real MACD-widening trades (7 plan stocks,
year 2) with the day's 5-min candles, EMA50, VWAP, BB, MACD, swing labels
(the engine's rule: 5 candles each side, so known 5 candles late), entry,
stop, target and exit. Writes chart_trades.json."""
import json
import sys

sys.path.insert(0, "D:/AI Trading/research/cross_widening_2026-10-27")
from common import ema_ok, macd_signals, minute_of_day, prepare  # noqa: E402

from mcommon import HERE, SEVEN, Y2_START, candles, rows_from_candles  # noqa: E402
from story.swings import SwingTracker  # noqa: E402

IST = 19800
data = prepare("5min")


def sim(rows, F, i, d):
    entry, atr = rows[i][4], F["atr"][i]
    stop, tgt = entry - d * 2 * atr, entry + d * 10 * atr
    day = (rows[i][0] + IST) // 86400
    best, j = 0.0, i
    while True:
        j += 1
        if j >= len(rows) or (rows[j][0] + IST) // 86400 != day:
            j -= 1
            return j, rows[j][4], "day end", stop, tgt, best
        ts, o, h, l, c, v = rows[j]
        if (d > 0 and l <= stop) or (d < 0 and h >= stop):
            return j, (min(o, stop) if d > 0 else max(o, stop)), "stop", stop, tgt, best
        if (d > 0 and h >= tgt) or (d < 0 and l <= tgt):
            return j, (max(o, tgt) if d > 0 else min(o, tgt)), "target", stop, tgt, best
        best = max(best, d * (c - entry) / entry * 100)


trades = []
for sym in sorted(SEVEN):
    rows, F = data[sym]
    for i, d, kind in macd_signals(F):
        a = F["adx"][i]
        if (kind == "widening" and rows[i][0] >= Y2_START and 570 <= minute_of_day(rows[i][0]) < 885
                and ema_ok(F, i, d) and a is not None and 30 <= a < 40 and d * F["di"][i] > 0):
            j, px, why, stop, tgt, best = sim(rows, F, i, d)
            if j == i:
                continue
            ret = d * (px - rows[i][4]) / rows[i][4] * 100
            trades.append(dict(sym=sym, i=i, j=j, d=d, ret=ret, why=why, stop=stop, tgt=tgt, best=best,
                               adx=a, px=px))

winners = sorted(trades, key=lambda t: -t["ret"])[:3]
uturns = sorted([t for t in trades if t["best"] >= 0.5 and t["ret"] <= 0], key=lambda t: -t["best"])[:3]
losers = [t for t in sorted(trades, key=lambda t: t["ret"]) if t["why"] == "stop" and t not in uturns][:2]
picked = [("Big winner", t) for t in winners] + [("U-turn", t) for t in uturns] + [("Stopped out", t) for t in losers]

out = []
cache = {}
for label, t in picked:
    sym = t["sym"]
    if sym not in cache:
        cs = candles(sym, "5min")
        rows = rows_from_candles(cs)
        sw = SwingTracker(mode="fixed", lookback=5)
        swings = []
        for k, r in enumerate(rows):
            for s in sw.update(r):
                swings.append((s.index, k, s.kind, s.price, s.label))
        cache[sym] = (cs, rows, swings)
    cs, rows, swings = cache[sym]
    i, j = t["i"], t["j"]
    day = (cs[i][0] + IST) // 86400
    idx = [k for k in range(max(0, i - 120), min(len(cs), j + 80)) if (cs[k][0] + IST) // 86400 == day]
    lo, hi = idx[0], idx[-1]

    def r4(x):
        return None if x is None else round(x, 4)
    out.append({
        "label": label, "sym": sym, "dir": "BUY" if t["d"] > 0 else "SELL",
        "date": __import__("datetime").datetime.utcfromtimestamp(cs[i][0] + IST).strftime("%d %b %Y"),
        "entry_t": cs[i][0] + IST, "entry": r4(cs[i][4]), "exit_t": cs[j][0] + IST, "exit": r4(t["px"]),
        "why": t["why"], "ret": round(t["ret"], 3), "best": round(t["best"], 3), "adx": round(t["adx"], 1),
        "stop": r4(t["stop"]), "tgt": r4(t["tgt"]),
        "candles": [[cs[k][0] + IST, cs[k][1], cs[k][2], cs[k][3], cs[k][4]] for k in idx],
        "ema50": [[cs[k][0] + IST, r4(rows[k]["ema50"])] for k in idx if rows[k]["ema50"]],
        "vwap": [[cs[k][0] + IST, r4(rows[k]["vwap"])] for k in idx if rows[k]["vwap"]],
        "bbu": [[cs[k][0] + IST, r4(rows[k]["bb_upper"])] for k in idx if rows[k]["bb_upper"]],
        "bbl": [[cs[k][0] + IST, r4(rows[k]["bb_lower"])] for k in idx if rows[k]["bb_lower"]],
        "macd": [[cs[k][0] + IST, r4(rows[k]["macd_line"]), r4(rows[k]["macd_signal"])] for k in idx
                 if rows[k]["macd_line"] is not None and rows[k]["macd_signal"] is not None],
        "swings": [{"t": cs[a][0] + IST, "known_t": cs[b][0] + IST, "kind": kind, "price": r4(p), "label": lab}
                   for a, b, kind, p, lab in swings if lo <= a <= hi and lab],
    })
json.dump(out, open(f"{HERE}/chart_trades.json", "w"))
for o in out:
    print(o["label"], o["sym"], o["date"], o["dir"], o["ret"], o["why"], "best", o["best"], "swings", len(o["swings"]))
