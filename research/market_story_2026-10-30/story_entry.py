"""Entry rules built from story movements (user, 2026-10-30): e.g. BB widening,
BB width >= 1%, volume above average, MACD crossover -> take the call.
PRELIMINARY: 13 stocks x 2 years, 5 min (rule: 15-30 stocks before deciding).

Signal at candle j (entries 09:30-14:45), direction = the MACD cross:
  cross      MACD crossed its signal line within the last 3 candles (one trade per cross)
  bbx        story bb_width state is "expanding"
  bbw>=X     BB width (upper - lower) / middle >= X % of price
  vol        volume above its 20-candle average
  trend      price on the trade side of a sloping EMA50 (10 candles)
  adx        ADX 30-40 with DI agreeing
Exit for all: stop 2xATR, target 10xATR, else day end. Money: Rs 5L pool,
Rs 2.5L per trade, itemized costs + 0.05% slippage."""
import pickle
import sys
from collections import defaultdict

from mcommon import HERE, SEVEN, SYMBOLS, Y2_START, candles, day, minute_of_day, rows_from_candles

sys.path.insert(0, "D:/AI Trading/AlgoTrading-signals/backend/scripts")
from order_backtest import itemized_round_trip_cost  # noqa: E402

SN = pickle.load(open(f"{HERE}/snaps_5min.pkl", "rb"))
CAPITAL, LEG, SLIP = 500_000, 250_000, 0.05
ROWS = {s: rows_from_candles(candles(s, "5min")) for s in SYMBOLS}
TS = {s: [c[0] for c in candles(s, "5min")] for s in SYMBOLS}


def features(sym):
    rows = ROWS[sym]
    out = []
    vols = []
    last_cross = (None, 0)  # (index, dir)
    for j, r in enumerate(rows):
        p = rows[j - 1] if j else None
        if p and None not in (r["macd_line"], r["macd_signal"], p["macd_line"], p["macd_signal"]):
            a, b = p["macd_line"] - p["macd_signal"], r["macd_line"] - r["macd_signal"]
            if a <= 0 < b:
                last_cross = (j, 1)
            elif a >= 0 > b:
                last_cross = (j, -1)
        vr = (r["volume"] / (sum(vols) / len(vols))) if len(vols) == 20 and sum(vols) else None
        vols.append(r["volume"])
        if len(vols) > 20:
            vols.pop(0)
        bbw = ((r["bb_upper"] - r["bb_lower"]) / r["bb_middle"] * 100) if r.get("bb_middle") else None
        e, e0 = r.get("ema50"), rows[j - 10].get("ema50") if j >= 10 else None
        tr = 0
        if e and e0:
            tr = 1 if (r["close"] > e and e > e0) else -1 if (r["close"] < e and e < e0) else 0
        out.append({"cross": last_cross, "vr": vr, "bbw": bbw, "trend": tr,
                    "adx": r.get("adx"), "di": (r.get("plus_di") or 0) - (r.get("minus_di") or 0),
                    "bbx": SN[sym][j].get(("bb_width", "state")) == "expanding"})
    return out


FEAT = {s: features(s) for s in SYMBOLS}


def signals(rule):
    out = []
    for sym in SYMBOLS:
        f, rows, ts = FEAT[sym], ROWS[sym], TS[sym]
        used = set()
        for j in range(len(rows)):
            if not (570 <= minute_of_day(ts[j]) < 885):
                continue
            ci, d = f[j]["cross"]
            if ci is None or j - ci > 2 or ci in used or day(ts[ci]) != day(ts[j]):
                continue
            if rule(f[j], d):
                used.add(ci)
                out.append((sym, j, d))
    return out


EXIT = ["atr", 2, 10]


def sim(sym, i, d):
    rows, ts = ROWS[sym], TS[sym]
    entry, atr = rows[i]["close"], rows[i]["atr"]
    if not atr:
        return None
    kind, a, b = EXIT
    if kind == "atr":
        stop, tgt = entry - d * a * atr, entry + d * b * atr
    else:
        stop, tgt = entry * (1 - d * a / 100), entry * (1 + d * b / 100)
    dy = day(ts[i])
    j = i
    while True:
        j += 1
        if j >= len(rows) or day(ts[j]) != dy:
            j -= 1
            if j == i:
                return None
            return d * (rows[j]["close"] - entry) / entry * 100, ts[j]
        r = rows[j]
        if (d > 0 and r["low"] <= stop) or (d < 0 and r["high"] >= stop):
            return d * ((min(r["open"], stop) if d > 0 else max(r["open"], stop)) - entry) / entry * 100, ts[j]
        if (d > 0 and r["high"] >= tgt) or (d < 0 and r["low"] <= tgt):
            return d * ((max(r["open"], tgt) if d > 0 else min(r["open"], tgt)) - entry) / entry * 100, ts[j]


def money(ts):
    cash, open_, net_sum = CAPITAL, [], 0.0
    n = 0
    for t0, t1, sym, e, d, ret in sorted(ts):
        keep = []
        for x in open_:
            if x[0] <= t0:
                cash += x[1] + x[2]
            else:
                keep.append(x)
        open_ = keep
        q = int(min(LEG, cash) // e)
        if any(x[3] == sym for x in open_) or q <= 0:
            continue
        ex = e * (1 + d * ret / 100)
        sell, buy = (ex, e) if d > 0 else (e, ex)
        net = q * (ex - e) * d - itemized_round_trip_cost(q, sell, buy) - q * e * SLIP / 100
        cash -= q * e
        open_.append((t1, q * e, net + q * e * SLIP / 100, sym))
        net_sum += net
        n += 1
    return n, net_sum


def adx_ok(f, d):
    return f["adx"] is not None and 30 <= f["adx"] < 40 and d * f["di"] > 0


def vol(f, x=1.0):
    return f["vr"] is not None and f["vr"] > x


RULES = {
    "cross only": lambda f, d: True,
    "cross + BB expanding + BB>=1% + vol>avg": lambda f, d: f["bbx"] and (f["bbw"] or 0) >= 1.0 and vol(f),
    "  BB>=1% ... + trend": lambda f, d: f["bbx"] and (f["bbw"] or 0) >= 1.0 and vol(f) and f["trend"] == d,
    "  BB>=1% ... + trend + ADX 30-40": lambda f, d: f["bbx"] and (f["bbw"] or 0) >= 1.0 and vol(f) and f["trend"] == d and adx_ok(f, d),
    "cross + BB expanding + BB>=0.5% + vol>avg": lambda f, d: f["bbx"] and (f["bbw"] or 0) >= 0.5 and vol(f),
    "  BB>=0.5% ... + trend": lambda f, d: f["bbx"] and (f["bbw"] or 0) >= 0.5 and vol(f) and f["trend"] == d,
    "cross + BB expanding + vol>1.5x avg + trend": lambda f, d: f["bbx"] and vol(f, 1.5) and f["trend"] == d,
    "cross + trend + ADX 30-40 (close to today's plan)": lambda f, d: f["trend"] == d and adx_ok(f, d),
}

EXITS = [("stop 2xATR / target 10xATR", ["atr", 2, 10]), ("stop 0.2% / target 0.5%", ["pct", 0.2, 0.5]),
         ("stop 0.4% / target 0.8%", ["pct", 0.4, 0.8]), ("stop 0.4% / target 1.2%", ["pct", 0.4, 1.2])]
SIGS = {name: signals(rule) for name, rule in RULES.items()}
for (ename, ex), (universe, uname) in [(e, u) for e in EXITS
                                       for u in ((SEVEN, "7 plan stocks"), (set(SYMBOLS), "all 13 stocks"))]:
    EXIT[:] = ex
    print(f"\n== {ename}, else day end | {uname}")
    print(f"{'rule':52s} {'signals':>7s} {'win':>6s} {'avg%':>7s} | {'Y1 trades':>9s} {'Y1 net':>9s} | "
          f"{'Y2 trades':>9s} {'Y2 net':>9s}")
    for name, rule in RULES.items():
        per = defaultdict(list)
        rets = []
        for sym, j, d in SIGS[name]:
            if sym not in universe:
                continue
            o = sim(sym, j, d)
            if not o:
                continue
            rets.append(o[0])
            per["Y2" if TS[sym][j] >= Y2_START else "Y1"].append((TS[sym][j], o[1], sym, ROWS[sym][j]["close"], d, o[0]))
        if not rets:
            print(f"{name:52s} none"); continue
        n1, y1 = money(per["Y1"])
        n2, y2 = money(per["Y2"])
        print(f"{name:52s} {len(rets):7d} {sum(r > 0 for r in rets) / len(rets):6.1%} "
              f"{sum(rets) / len(rets):+7.3f} | {n1:9d} {y1:9,.0f} | {n2:9d} {y2:9,.0f}", flush=True)
print("\nNet = after itemized costs and 0.05% slippage; Rs 5L pool, Rs 2.5L per trade.")
