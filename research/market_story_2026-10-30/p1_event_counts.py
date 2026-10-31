"""How many P1 point events per stock per day (5 and 3 min), by event."""
from collections import Counter
from mcommon import SYMBOLS, candles, day, rows_from_candles
from story.indicator_rows import ist_date
from story.point_events import DayLevels, line_events

for tf in ("5min", "3min"):
    cnt, days = Counter(), 0
    for sym in SYMBOLS:
        rows = rows_from_candles(candles(sym, tf))
        dl, prev = DayLevels(), None
        days += len({ist_date(r["ts"]) for r in rows})
        for r in rows:
            for _, name, _ in line_events(prev, r) + dl.update(ist_date(r["ts"]), r["high"], r["low"], r["close"]):
                cnt[name] += 1
            prev = r
    tot = sum(cnt.values())
    print(f"== {tf}: {tot / days:.1f} events per stock-day")
    by_kind = Counter()
    for k, v in cnt.items():
        by_kind[k.rsplit("_", 1)[-1] if "_" in k else k] += v
    print("   by suffix:", ", ".join(f"{k}={v / days:.1f}" for k, v in by_kind.most_common()))
    print("   top:", ", ".join(f"{k}={v / days:.1f}" for k, v in cnt.most_common(8)))
