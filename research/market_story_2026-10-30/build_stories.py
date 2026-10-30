"""Story rows for every stock and day (13 stocks x 2 years, 3 and 5 min).

Saves stories_<tf>.pkl: {sym: {"runs": [run dicts], "ts": [epoch per row]}}
and, for 5 min only, snaps_5min.pkl: {sym: [snapshot per row]} for the
prediction unit. Prints rows per stock per day and time per candle."""
import pickle
import time

from mcommon import HERE, SYMBOLS, candles, day, rows_from_candles, run_tracker

for tf in ("5min", "3min"):
    out, snaps_all = {}, {}
    n_candles = n_runs = 0
    t_track = 0.0
    days = set()
    for sym in SYMBOLS:
        cs = candles(sym, tf)
        rows = rows_from_candles(cs)
        t0 = time.perf_counter()
        runs, snaps, _ = run_tracker(rows, keep_snaps=(tf == "5min"))
        t_track += time.perf_counter() - t0
        out[sym] = {"runs": [dict(r.to_dict(), start_index=r.start_index, end_index=r.end_index) for r in runs],
                    "ts": [c[0] for c in cs]}
        if snaps is not None:
            snaps_all[sym] = snaps
        n_candles += len(rows)
        n_runs += len(runs)
        sym_days = {day(c[0]) for c in cs}
        days |= sym_days
        print(f"{tf} {sym:11s} candles={len(rows):6d} runs={len(runs):7d} "
              f"runs/day={len(runs) / max(1, len(sym_days)):6.1f}", flush=True)
    pickle.dump(out, open(f"{HERE}/stories_{tf}.pkl", "wb"))
    if snaps_all:
        pickle.dump(snaps_all, open(f"{HERE}/snaps_5min.pkl", "wb"))
    print(f"{tf}: {n_candles} candles, {n_runs} runs, tracker time {t_track:.1f}s = "
          f"{t_track / n_candles * 1e6:.0f} us per candle", flush=True)
