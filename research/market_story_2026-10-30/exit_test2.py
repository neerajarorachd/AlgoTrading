"""Story-based exits (user, 2026-10-30): once a trade is open, the story
watches it; when the setup turns against the trade, exit.

Trades: the Rs 5L plan setup (5-min MACD cross-widening, EMA50 side + slope,
ADX 30-40 with DI agreeing, entries 09:30-14:45), all 13 stocks, both years.
Every exit keeps the 2xATR stop and 10xATR target; story exits come on top,
at the close of the candle where the rule fires (from the 2nd candle on).

Rules
  base       stop / target / day end (today's plan)
  line       price closes on the wrong side of VWAP or EMA50 (story: below for a buy)
  fade       BB width squeezing AND MACD narrowing or crossed back
  k of N     k or more of the directional movements against the trade (below)
  predict    setup prediction for the trade's direction falls under 0.7x its
             MACD-group average (learned on year 1, so year 2 only)

Movements counted as "against" a buy (mirror for a sell): price below VWAP /
EMA50 / MA50 / MA21; VWAP / EMA50 / MA50 slope falling; MACD bear; MACD below
zero; RSI under 40; Stoch %K under %D; ADX with -DI leading; structure
downtrend; near the day low; below the previous day's low; BB squeezing."""
import pickle
import sys
from collections import defaultdict

sys.path.insert(0, "D:/AI Trading/research/cross_widening_2026-10-27")
from common import macd_signals, ema_ok, minute_of_day, prepare  # noqa: E402
sys.path.insert(0, "D:/AI Trading/AlgoTrading-signals/backend/scripts")
from order_backtest import itemized_round_trip_cost  # noqa: E402
sys.path.insert(0, "D:/AI Trading/AlgoTrading-story/backend")
from story.setup import EvidenceIndex  # noqa: E402

HERE = "D:/AI Trading/research/market_story_2026-10-30"
Y2_START = 1758067200
IST = 19800
CAPITAL, LEG, SLIP = 500_000, 250_000, 0.05
SEVEN = {"ASHOKLEY", "BHEL", "CUB", "FINCABLES", "HINDCOPPER", "NATIONALUM", "RELIANCE"}
SN = pickle.load(open(f"{HERE}/snaps_5min.pkl", "rb"))
data = prepare("5min")


def st(s, subj):
    return s.get((subj, "state"))


def against(s, d):
    """Movements against direction d (+1 buy / -1 sell) in snapshot s."""
    bad = "below" if d > 0 else "above"
    fall = "falling" if d > 0 else "rising"
    out = []
    for line in ("vwap", "ema50", "ma50", "ma21"):
        if st(s, f"price_vs_{line}") == bad:
            out.append(f"price_vs_{line}")
    for subj in ("vwap_slope", "ema50_slope", "ma50_slope"):
        if st(s, subj) == fall:
            out.append(subj)
    m = st(s, "macd") or ""
    if m.startswith("bear" if d > 0 else "bull"):
        out.append("macd")
    if st(s, "macd_zero") == bad:
        out.append("macd_zero")
    r = st(s, "rsi")
    if (d > 0 and r in ("lt30", "30_40")) or (d < 0 and r in ("60_70", "70p")):
        out.append("rsi")
    if st(s, "stoch_cross") == ("k_under_d" if d > 0 else "k_over_d"):
        out.append("stoch")
    a = st(s, "adx") or ""
    if a.endswith("minus" if d > 0 else "plus"):
        out.append("adx")
    if st(s, "structure") == ("downtrend" if d > 0 else "uptrend"):
        out.append("structure")
    if st(s, "day_range") == ("near_low" if d > 0 else "near_high"):
        out.append("day_range")
    if st(s, "prev_day") == ("below_low" if d > 0 else "above_high"):
        out.append("prev_day")
    if st(s, "bb_width") == "squeezing":
        out.append("bb_squeezing")
    return out


def rule_line(s, d, ctx):
    bad = "below" if d > 0 else "above"
    return st(s, "price_vs_vwap") == bad or st(s, "price_vs_ema50") == bad


def rule_fade(s, d, ctx):
    m = st(s, "macd") or ""
    return st(s, "bb_width") == "squeezing" and (m.endswith("narrowing") or not m.startswith("bull" if d > 0 else "bear"))


def rule_k(k):
    return lambda s, d, ctx: len(against(s, d)) >= k


# setup prediction learned on year 1 (same question as predict_unit: 0.5% before 0.25% in 12 candles)
from predict_unit_labels import train_index  # noqa: E402
IDX = train_index()


def rule_predict(s, d, ctx):
    side = "up" if d > 0 else "dn"
    idx = IDX[side]
    p = idx.predict(s)
    if not p:
        return False
    n0, w0 = idx.base.get(s.get(("macd", "state")), (0, 0))
    g = w0 / n0 if n0 else 0
    prob = (p["prob"] * p["cases"] + 30 * g) / (p["cases"] + 30)
    return prob < 0.7 * ctx["entry_base"][side]


BASE_RULES = {"line": rule_line, "fade": rule_fade, "4 of N": rule_k(4), "6 of N": rule_k(6),
              "predict": rule_predict}
RULES = {"base": (None, "exit")}
for mode in ("losing", "protect"):
    for k, r in BASE_RULES.items():
        RULES[f"{k} {mode}"] = (r, mode)


ctx_mode = ["exit"]


def simulate(sym, rows, F, i, d, rule):
    entry, atr = rows[i][4], F["atr"][i]
    if not atr:
        return None
    stop, tgt = entry - d * 2 * atr, entry + d * 10 * atr
    day = (rows[i][0] + IST) // 86400
    snaps = SN[sym]
    ctx = {"entry_base": IDX["base"]}
    j = i
    why = "day end"
    while True:
        j += 1
        if j >= len(rows) or (rows[j][0] + IST) // 86400 != day:
            j -= 1
            if j == i:
                return None
            px = rows[j][4]
            break
        ts, o, h, l, c, v = rows[j]
        if (d > 0 and l <= stop) or (d < 0 and h >= stop):
            px, why = (min(o, stop) if d > 0 else max(o, stop)), ("breakeven" if why == "protected" else "stop")
            break
        if (d > 0 and h >= tgt) or (d < 0 and l <= tgt):
            px, why = (max(o, tgt) if d > 0 else min(o, tgt)), "target"
            break
        if rule and j - i >= 2 and rule(snaps[j], d, ctx):
            in_profit = d * (c - entry) > 0
            mode = ctx_mode[0]
            if mode == "exit" or (mode == "losing" and not in_profit):
                px, why = c, "story"
                break
            if mode == "protect":
                if in_profit:
                    stop = max(stop, entry) if d > 0 else min(stop, entry)
                    why = "protected"
                else:
                    px, why = c, "story"
                    break
    return d * (px - entry) / entry * 100, rows[j][0], why


trades = []  # (t0, sym, i, d)
for sym, (rows, F) in data.items():
    for i, d, kind in macd_signals(F):
        a = F["adx"][i]
        if (kind == "widening" and 570 <= minute_of_day(rows[i][0]) < 885 and ema_ok(F, i, d)
                and a is not None and 30 <= a < 40 and d * F["di"][i] > 0):
            trades.append((rows[i][0], sym, i, d))


def money(ts):
    S = sorted(ts)
    cash, open_, net_sum, slip_sum = CAPITAL, [], 0.0, 0.0
    for t0, t1, sym, e, d, ret in S:
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


base_ret = {}
for universe, uname in ((SEVEN, "7 plan stocks"), (set(data), "all 13 stocks")):
    print(f"\n== {uname}: net Rs after costs and slippage, Rs 5L pool, Rs 2.5L per trade")
    print(f"{'rule':16s} {'Y1 net':>10s} {'Y2 net':>10s} {'avg%':>7s} {'win':>6s} {'story exits':>11s} "
          f"{'big winners cut':>15s} {'losers improved':>15s}")
    for name, (rule, mode) in RULES.items():
        ctx_mode[0] = mode
        per_year = defaultdict(list)
        rets, wins, story_n, cut, saved = [], 0, 0, 0, 0
        for t0, sym, i, d in trades:
            if sym not in universe:
                continue
            if name.startswith("predict") and t0 < Y2_START:
                continue
            rows, F = data[sym]
            o = simulate(sym, rows, F, i, d, rule)
            if not o:
                continue
            ret, t1, why = o
            key = (sym, i)
            if name == "base":
                base_ret[key] = ret
            b = base_ret.get(key)
            if b is not None and name != "base":
                cut += b >= 1.0 and ret < b - 0.3
                saved += b < 0 and ret > b + 0.1
            story_n += why in ("story", "breakeven")
            rets.append(ret)
            wins += ret > 0
            per_year["Y2" if t0 >= Y2_START else "Y1"].append((t0, t1, sym, rows[i][4], d, ret))
        y1 = money(per_year["Y1"]) if per_year["Y1"] else float("nan")
        y2 = money(per_year["Y2"])
        print(f"{name:16s} {y1:10,.0f} {y2:10,.0f} {sum(rets) / len(rets):+7.3f} {wins / len(rets):6.1%} "
              f"{story_n:11d} {cut:15d} {saved:15d}")
