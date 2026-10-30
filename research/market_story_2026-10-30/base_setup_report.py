"""Today's setup with its own exit, no U-turn rule: 5-min MACD cross-widening,
EMA50 side + slope, ADX 30-40 with DI agreeing, entries 09:30-14:45, stop
2xATR, target 10xATR, else day end. Rs 5L, no leverage, shared pool, Rs 2.5L
per trade, itemized costs, slippage 0.05% shown separately. Per year: trades,
win rate, average win / loss, exit reasons, net, drawdown, months."""
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, "D:/AI Trading/research/market_story_2026-10-30")
from uturn_test import SEVEN, Y2_START, IST, CAPITAL, LEG, SLIP, data, trades, itemized_round_trip_cost  # noqa: E402

RULE_STOP, RULE_TGT = 2, 10


def sim(sym, i, d):
    rows, F = data[sym]
    entry, atr = rows[i][4], F["atr"][i]
    stop, tgt = entry - d * RULE_STOP * atr, entry + d * RULE_TGT * atr
    day = (rows[i][0] + IST) // 86400
    j = i
    while True:
        j += 1
        if j >= len(rows) or (rows[j][0] + IST) // 86400 != day:
            j -= 1
            if j == i:
                return None
            return d * (rows[j][4] - entry) / entry * 100, rows[j][0], "day end"
        ts, o, h, l, c, v = rows[j]
        if (d > 0 and l <= stop) or (d < 0 and h >= stop):
            return d * ((min(o, stop) if d > 0 else max(o, stop)) - entry) / entry * 100, ts, "stop"
        if (d > 0 and h >= tgt) or (d < 0 and l <= tgt):
            return d * ((max(o, tgt) if d > 0 else min(o, tgt)) - entry) / entry * 100, ts, "target"


def report(universe, label):
    print(f"\n== {label}")
    per = defaultdict(list)
    for t0, sym, i, d in trades:
        if sym in universe:
            o = sim(sym, i, d)
            if o:
                per["Y2" if t0 >= Y2_START else "Y1"].append((t0, o[1], sym, data[sym][0][i][4], d, o[0], o[2]))
    for y in ("Y1", "Y2"):
        S = sorted(per[y])
        cash, open_, net_sum, slip_sum, cost_sum = CAPITAL, [], 0.0, 0.0, 0.0
        taken, skipped, wins, losses, reasons = [], 0, [], [], defaultdict(int)
        eq = peak = dd = 0.0
        months = defaultdict(float)
        for t0, t1, sym, e, d, ret, why in S:
            keep = []
            for x in open_:
                if x[0] <= t0:
                    cash += x[1] + x[2]
                else:
                    keep.append(x)
            open_ = keep
            q = int(min(LEG, cash) // e)
            if any(x[3] == sym for x in open_) or q <= 0:
                skipped += 1
                continue
            ex = e * (1 + d * ret / 100)
            sell, buy = (ex, e) if d > 0 else (e, ex)
            cost = itemized_round_trip_cost(q, sell, buy)
            slip = q * e * SLIP / 100
            net = q * (ex - e) * d - cost - slip
            cash -= q * e
            open_.append((t1, q * e, net + slip, sym))
            taken.append(net)
            (wins if net > 0 else losses).append(net)
            reasons[why] += 1
            cost_sum += cost
            slip_sum += slip
            net_sum += net
            eq += net
            peak = max(peak, eq)
            dd = min(dd, eq - peak)
            m = datetime.fromtimestamp(t0 + IST, tz=timezone.utc)
            months[(m.year, m.month)] += net
        n = len(taken)
        aw = sum(wins) / len(wins) if wins else 0
        al = sum(losses) / len(losses) if losses else 0
        print(f"  {y}: {n} trades taken ({skipped} skipped, money busy or same stock open), "
              f"win rate {len(wins) / n:.1%}, avg win Rs {aw:,.0f}, avg loss Rs {al:,.0f} "
              f"(win/loss size {abs(aw / al):.2f}x)")
        print(f"      exits: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())) +
              f" | costs Rs {cost_sum:,.0f}, slippage Rs {slip_sum:,.0f}")
        print(f"      NET after costs + slippage Rs {net_sum:,.0f} = {net_sum / CAPITAL:+.1%} on Rs 5L | "
              f"worst drawdown Rs {dd:,.0f} | months positive {sum(v > 0 for v in months.values())}/{len(months)}, "
              f"worst month Rs {min(months.values()):,.0f}, best Rs {max(months.values()):,.0f}")


report(SEVEN, "7 plan stocks (ASHOKLEY, BHEL, CUB, FINCABLES, HINDCOPPER, NATIONALUM, RELIANCE)")
report(set(data), "all 13 stocks")
