"""Does the setup prediction improve the MACD-widening trend trades? Year 2 only
(predictions learned on year 1).

Trades: the ₹5L plan's setup (5 min MACD cross-widening, EMA50 side + slope,
ADX 30-40 with DI agreeing, entries 09:30-14:45, stop 2xATR, target 10xATR,
else day end). For each trade, the prediction for its direction (up for buy,
down for sell). Filter = keep the trade only if the prediction is above the
year-1 average for that direction. Money: Rs 5L shared pool, Rs 2.5L per
trade, itemized costs; slippage 0.05% shown separately."""
import pickle
import sys
from collections import defaultdict

sys.path.insert(0, "D:/AI Trading/research/cross_widening_2026-10-27")
from common import macd_signals, ema_ok, minute_of_day, prepare, simulate  # noqa: E402
sys.path.insert(0, "D:/AI Trading/AlgoTrading-signals/backend/scripts")
from order_backtest import itemized_round_trip_cost  # noqa: E402

HERE = "D:/AI Trading/research/market_story_2026-10-30"
Y2_START = 1758067200
SEVEN = {"ASHOKLEY", "BHEL", "CUB", "FINCABLES", "HINDCOPPER", "NATIONALUM", "RELIANCE"}
RULE = {"stop": ("atr", 2), "target": ("atr", 10)}
CAPITAL, LEG, SLIP = 500_000, 250_000, 0.05

P = pickle.load(open(f"{HERE}/predict_y2.pkl", "rb"))
pos = {k: n for n, k in enumerate(P["key"])}
data = prepare("5min")

trades = []  # (t0, t1, sym, entry, dir, ret%, preds{variant: p}, base)
missing = 0
for sym, (rows, F) in data.items():
    for i, d, kind in macd_signals(F):
        if kind != "widening" or rows[i][0] < Y2_START:
            continue
        a = F["adx"][i]
        if not (570 <= minute_of_day(rows[i][0]) < 885 and ema_ok(F, i, d)
                and a is not None and 30 <= a < 40 and d * F["di"][i] > 0):
            continue
        o = simulate(rows, F, i, d, RULE)
        if not o:
            continue
        side = "up" if d > 0 else "dn"
        n = pos.get((sym, i))
        if n is None:
            missing += 1
            continue
        pr = {v: float(P["preds"][side][v][n]) for v in P["preds"][side]}
        trades.append((rows[i][0], rows[i + o[1]][0], sym, rows[i][4], d, o[0], pr, P["base"][side]))
print(f"year-2 trades: {len(trades)} (13 stocks), {sum(t[2] in SEVEN for t in trades)} on the 7 plan stocks; "
      f"{missing} had no prediction")


def money(ts):
    S = sorted(ts, key=lambda s: (s[0], s[2]))
    cash, open_, net_sum, slip_sum, n, skipped = CAPITAL, [], 0.0, 0.0, 0, 0
    for t0, t1, sym, e, d, ret, *_ in S:
        keep = []
        for x in open_:
            if x[0] <= t0:
                cash += x[1] + x[2]
            else:
                keep.append(x)
        open_ = keep
        if any(x[3] == sym for x in open_):
            skipped += 1
            continue
        q = int(min(LEG, cash) // e)
        if q <= 0:
            skipped += 1
            continue
        ex = e * (1 + d * ret / 100)
        sell, buy = (ex, e) if d > 0 else (e, ex)
        net = q * (ex - e) * d - itemized_round_trip_cost(q, sell, buy)
        cash -= q * e
        open_.append((t1, q * e, net, sym))
        net_sum += net
        slip_sum += q * e * SLIP / 100
        n += 1
    return n, net_sum, net_sum - slip_sum


def line(label, ts):
    if not ts:
        print(f"  {label:34s} no trades"); return
    rets = [t[5] for t in ts]
    n, net, net_s = money(ts)
    print(f"  {label:34s} trades {len(ts):4d}  win {sum(r > 0 for r in rets) / len(ts):5.1%}  "
          f"avg {sum(rets) / len(ts):+.3f}%  | Rs 5L pool: {n:3d} taken, net Rs {net:9,.0f}, "
          f"after slippage Rs {net_s:9,.0f}")


for universe, label in ((SEVEN, "7 plan stocks"), (set(data), "all 13 stocks")):
    U = [t for t in trades if t[2] in universe]
    print(f"\n== {label}, year 2")
    line("all trades (today's plan)", U)
    for v in ("macd", "backoff300s", "gbm"):
        hi = [t for t in U if t[6][v] > t[7]]
        lo = [t for t in U if t[6][v] <= t[7]]
        line(f"{v}: kept (above average)", hi)
        line(f"{v}: dropped", lo)
    # halves by the model's own ranking (no threshold choice)
    for v in ("backoff300s", "gbm"):
        o = sorted(U, key=lambda t: -t[6][v])
        h = len(o) // 2
        line(f"{v}: top half", o[:h])
        line(f"{v}: bottom half", o[h:])
