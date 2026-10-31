"""Money from the early-reversal sequence elements (LL->HH->HL->HH and mirror).
PRELIMINARY: 13 stocks x 2 years, 5 min. Entry at the close of the candle
where the 4th swing is confirmed, Rs 2.5L per trade, itemized NSE intraday
costs + 0.05% slippage. Exits: hold 12 candles / day end / stop 2xATR + target
10xATR else day end. Net Rs per year (2 years of data -> divided by 2)."""
import sys

sys.path.insert(0, "D:/AI Trading/AlgoTrading-signals/backend/scripts")
from order_backtest import itemized_round_trip_cost  # noqa: E402

from mcommon import SYMBOLS, candles, day, rows_from_candles  # noqa: E402
from sequence_success_elements import SN, elements  # noqa: E402  (re-runs that scan's prints)
from story.swings import SwingTracker  # noqa: E402

LEG, SLIP = 250_000, 0.05
GROUPS = {
    "all sequences": lambda e: True,
    "ADX 20-30, old DI leading": lambda e: "adx=20_30_against" in e,
    "beyond yesterday's opposite level": lambda e: "prev_day=against" in e,
    "VWAP still sloping the old way": lambda e: "vwap_slope=against" in e,
    "RSI still on the old side": lambda e: "rsi=against" in e,
    "any of the 4 above": lambda e: bool(e & {"adx=20_30_against", "prev_day=against",
                                               "vwap_slope=against", "rsi=against"}),
    "close near the day's extreme": lambda e: "day_range=with" in e,
}


def exit_ret(cs, rows, k, d, mode):
    entry, atr = cs[k][4], rows[k]["atr"] or 0
    dk = day(cs[k][0])
    stop, tgt = entry - d * 2 * atr, entry + d * 10 * atr
    j = k
    while j + 1 < len(cs) and day(cs[j + 1][0]) == dk:
        j += 1
        ts, o, h, l, c, v = cs[j]
        if mode == "12 candles" and j - k >= 12:
            return d * (c - entry) / entry * 100
        if mode == "stop/target" and atr:
            if (d > 0 and l <= stop) or (d < 0 and h >= stop):
                return d * ((min(o, stop) if d > 0 else max(o, stop)) - entry) / entry * 100
            if (d > 0 and h >= tgt) or (d < 0 and l <= tgt):
                return d * ((max(o, tgt) if d > 0 else min(o, tgt)) - entry) / entry * 100
    return None if j == k else d * (cs[j][4] - entry) / entry * 100


def net_rs(entry, ret, d):
    q = int(LEG // entry)
    ex = entry * (1 + d * ret / 100)
    sell, buy = (ex, entry) if d > 0 else (entry, ex)
    return q * (ex - entry) * d - itemized_round_trip_cost(q, sell, buy) - q * entry * SLIP / 100


trades = []  # (elements, entry, d, {mode: ret})
for sym in SYMBOLS:
    cs = candles(sym, "5min")
    rows = rows_from_candles(cs)
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
            rets = {m: exit_ret(cs, rows, k4, d, m) for m in ("12 candles", "day end", "stop/target")}
            if None in rets.values():
                continue
            trades.append((elements(SN[sym][k4], d), cs[k4][4], d, rets))

print("\n\n=========== MONEY ===========")
print(f"{'group':36s} {'trades/yr':>9s} | " + " | ".join(f"{m:^31s}" for m in ("12 candles", "day end", "stop/target")))
print(f"{'':36s} {'':9s} | " + " | ".join(f"{'win  gross%  net%/trade  Rs/yr':31s}" for _ in range(3)))
for name, fn in GROUPS.items():
    ts = [t for t in trades if fn(t[0])]
    cells = []
    for m in ("12 candles", "day end", "stop/target"):
        rets = [t[3][m] for t in ts]
        nets = [net_rs(t[1], t[3][m], t[2]) for t in ts]
        net_pct = sum(nets) / len(nets) / LEG * 100
        cells.append(f"{sum(r > 0 for r in rets) / len(rets):4.0%} {sum(rets) / len(rets):+6.3f} {net_pct:+8.3f}   "
                     f"{sum(nets) / 2:8,.0f}")
    print(f"{name:36s} {len(ts) / 2:9.0f} | " + " | ".join(f"{c:31s}" for c in cells))
print("\nRs/yr = net after costs and slippage at Rs 2.5L per trade, all trades taken (2 years / 2).")
