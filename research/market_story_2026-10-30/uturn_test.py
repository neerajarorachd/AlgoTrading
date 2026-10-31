"""U-turn exits (user, 2026-10-30): trades that went into profit and then turned.
At what drop from the best point does exiting help instead of hurt?
PRELIMINARY: 13 stocks (rule: 15-30 stocks x 2 years before deciding).

Trades: the Rs 5L plan's MACD-widening setup, 5 min, both years. Every
variant keeps the 2xATR stop and 10xATR target; the U-turn exit is added on
top and only switches on once the trade's best close is at least `arm`%
in profit:
  giveback G   exit when the close has given back G of the best profit
  ATR k        exit when the close is k x ATR below the best close (buy)
  +story       the same, but only if 4+ story movements are against the trade
Part 1 describes U-turns (best profit reached vs what the trade ended with).
Part 2: net Rs (costs + 0.05% slippage, Rs 5L pool, Rs 2.5L per trade), Y1 / Y2,
and per trade vs the base: helped (exit better by > 0.1%) / hurt."""
import pickle
import sys
from collections import defaultdict

sys.path.insert(0, "D:/AI Trading/research/cross_widening_2026-10-27")
from common import macd_signals, ema_ok, minute_of_day, prepare  # noqa: E402
sys.path.insert(0, "D:/AI Trading/AlgoTrading-signals/backend/scripts")
from order_backtest import itemized_round_trip_cost  # noqa: E402

HERE = "D:/AI Trading/research/market_story_2026-10-30"
Y2_START, IST = 1758067200, 19800
CAPITAL, LEG, SLIP = 500_000, 250_000, 0.05
SEVEN = {"ASHOKLEY", "BHEL", "CUB", "FINCABLES", "HINDCOPPER", "NATIONALUM", "RELIANCE"}
SN = pickle.load(open(f"{HERE}/snaps_5min.pkl", "rb"))
data = prepare("5min")


def n_against(s, d):
    def st(x):
        return s.get((x, "state"))
    bad, fall = ("below", "falling") if d > 0 else ("above", "rising")
    n = sum(st(f"price_vs_{l}") == bad for l in ("vwap", "ema50", "ma50", "ma21"))
    n += sum(st(x) == fall for x in ("vwap_slope", "ema50_slope", "ma50_slope"))
    n += (st("macd") or "").startswith("bear" if d > 0 else "bull")
    n += st("macd_zero") == bad
    n += (st("adx") or "").endswith("minus" if d > 0 else "plus")
    n += st("structure") == ("downtrend" if d > 0 else "uptrend")
    n += st("bb_width") == "squeezing"
    return n


def simulate(sym, i, d, v):
    rows, F = data[sym]
    entry, atr = rows[i][4], F["atr"][i]
    if not atr:
        return None
    stop, tgt = entry - d * 2 * atr, entry + d * 10 * atr
    day = (rows[i][0] + IST) // 86400
    best = entry
    j = i
    while True:
        j += 1
        if j >= len(rows) or (rows[j][0] + IST) // 86400 != day:
            j -= 1
            if j == i:
                return None
            px = rows[j][4]
            break
        ts, o, h, l, c, vol = rows[j]
        if (d > 0 and l <= stop) or (d < 0 and h >= stop):
            px = min(o, stop) if d > 0 else max(o, stop)
            break
        if (d > 0 and h >= tgt) or (d < 0 and l <= tgt):
            px = max(o, tgt) if d > 0 else min(o, tgt)
            break
        best = max(best, c) if d > 0 else min(best, c)
        peak = d * (best - entry) / entry * 100
        if v and peak >= v["arm"]:
            now = d * (c - entry) / entry * 100
            if "give" in v:
                trig = now <= peak * (1 - v["give"])
            else:
                trig = d * (best - c) >= v["atr"] * atr
            if trig and v.get("story") and n_against(SN[sym][j], d) < 4:
                trig = False
            if trig:
                px = c
                break
    return d * (px - entry) / entry * 100, rows[j][0]


def path_stats(sym, i, d):
    """best close profit % and final % under the base exit."""
    rows, F = data[sym]
    o = simulate(sym, i, d, None)
    if not o:
        return None
    entry = rows[i][4]
    day = (rows[i][0] + IST) // 86400
    best, j = 0.0, i
    while rows[j][0] < o[1]:
        j += 1
        if j >= len(rows) or (rows[j][0] + IST) // 86400 != day:
            break
        best = max(best, d * (rows[j][4] - entry) / entry * 100)
    return best, o[0]


trades = []
for sym, (rows, F) in data.items():
    for i, d, kind in macd_signals(F):
        a = F["adx"][i]
        if (kind == "widening" and 570 <= minute_of_day(rows[i][0]) < 885 and ema_ok(F, i, d)
                and a is not None and 30 <= a < 40 and d * F["di"][i] > 0):
            trades.append((rows[i][0], sym, i, d))


def money(ts):
    cash, open_, net_sum, slip_sum = CAPITAL, [], 0.0, 0.0
    for t0, t1, sym, e, d, ret in sorted(ts):
        keep = []
        for x in open_:
            if x[0] <= t0:
                cash += x[1] + x[2]
            else:
                keep.append(x)
        open_ = keep
        if any(x[3] == sym for x in open_):
            continue
        q = int(min(LEG, cash) // e)
        if q <= 0:
            continue
        ex = e * (1 + d * ret / 100)
        sell, buy = (ex, e) if d > 0 else (e, ex)
        net = q * (ex - e) * d - itemized_round_trip_cost(q, sell, buy)
        cash -= q * e
        open_.append((t1, q * e, net, sym))
        net_sum += net
        slip_sum += q * e * SLIP / 100
    return net_sum - slip_sum


print("== Part 1: U-turns. Trades by the best profit they reached (close basis), all 13 stocks, 2 years")
print(f"{'best profit':12s} {'trades':>6s} {'ended avg':>9s} {'ended < 0':>9s} {'gave back >50%':>14s}")
buckets = [(0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.5), (2.5, 99)]
ps = [x for x in (path_stats(s, i, d) for _, s, i, d in trades) if x]
for lo, hi in buckets:
    xs = [(b, f) for b, f in ps if lo <= b < hi]
    if xs:
        print(f"{lo:>4}-{hi:<6}%  {len(xs):6d} {sum(f for _, f in xs) / len(xs):+9.3f} "
              f"{sum(f < 0 for _, f in xs) / len(xs):9.0%} {sum(b > 0 and f < b / 2 for b, f in xs) / len(xs):14.0%}")

VARIANTS = {"base": None}
for arm in (0.5, 1.0, 1.5):
    for give in (0.33, 0.5, 0.67):
        VARIANTS[f"arm {arm}% giveback {give:.0%}"] = {"arm": arm, "give": give}
    for k in (1.5, 3.0):
        VARIANTS[f"arm {arm}% {k}xATR from best"] = {"arm": arm, "atr": k}
for arm in (0.5, 1.0):
    for give in (0.33, 0.5):
        VARIANTS[f"arm {arm}% giveback {give:.0%} +story"] = {"arm": arm, "give": give, "story": True}

base = {}
for universe, uname in ((SEVEN, "7 plan stocks"), (set(data), "all 13 stocks")):
    print(f"\n== Part 2, {uname}: net Rs Y1 / Y2, and trades helped / hurt vs base")
    print(f"{'variant':32s} {'Y1 net':>9s} {'Y2 net':>9s} {'helped':>7s} {'hurt':>6s}")
    for name, v in VARIANTS.items():
        per = defaultdict(list)
        helped = hurt = 0
        for t0, sym, i, d in trades:
            if sym not in universe:
                continue
            o = simulate(sym, i, d, v)
            if not o:
                continue
            if v is None:
                base[(sym, i)] = o[0]
            else:
                b = base[(sym, i)]
                helped += o[0] > b + 0.1
                hurt += o[0] < b - 0.1
            per["Y2" if t0 >= Y2_START else "Y1"].append((t0, o[1], sym, data[sym][0][i][4], d, o[0]))
        print(f"{name:32s} {money(per['Y1']):9,.0f} {money(per['Y2']):9,.0f} {helped:7d} {hurt:6d}", flush=True)
