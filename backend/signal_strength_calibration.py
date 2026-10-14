"""Calibrates signal-strength minimums from history (ALGO-6) -- used by the
API (preview, then apply) and by scripts/compute_signal_strength_defaults.py.

- 1-min is the base. Each signal's measures are calibrated TOGETHER so about
  `target` of its formations qualify overall (a per-measure percentile would
  compound: 40% on each of 3 measures leaves ~8% passing all three).
- 3-min / 5-min multipliers = median range % of 3/5-min candles (rolled up
  from 1-min on the 09:15 grid) / median 1-min range %.

`compute` only reads; `apply` writes the system minimums and multipliers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from condition_evaluator import EVENTS_COLUMN, TRADING_DATE_COLUMN, build_indicator_dataframe, parse_and_evaluate
from db.ops import LibSignalStrength, LibSystemSettings
from db.ops.LibSymbols import get_active
from db.session import session_scope
from signal_strength import STRENGTH_MEASURES

MARKET_OPEN_MINUTE = 9 * 60 + 15


def _rolled_range_pct(df: pd.DataFrame, minutes: int) -> pd.Series:
    ist = pd.to_datetime(df["ts"]).dt.tz_convert("Asia/Kolkata")
    offset = ((ist.dt.hour * 60 + ist.dt.minute - MARKET_OPEN_MINUTE) // minutes) * minutes + MARKET_OPEN_MINUTE
    bucket = ist.dt.floor("D") + pd.to_timedelta(offset, unit="m")
    g = df.groupby(bucket)
    return (g["high"].max() - g["low"].min()) / g["close"].last() * 100


def _calibrate_signal(measures: pd.DataFrame, target: float):
    """Per-measure percentile q at which ~target of formations pass every measure."""
    def share_at(mins):  # unmeasurable values (NaN) don't block -- same rule as EVENT()
        return ((measures >= mins) | measures.isna()).all(axis=1).mean()
    lo, hi = 0.0, 1.0
    for _ in range(30):
        q = (lo + hi) / 2
        lo, hi = (q, hi) if share_at(measures.quantile(q)) > target else (lo, q)
    mins = measures.quantile((lo + hi) / 2)
    return mins, float(share_at(mins))


def compute(session_factory, target: float = 0.40, sessions: int = 20, now=None) -> dict:
    """Read-only. Returns {base: {signal: {measure: min}}, multipliers: {...},
    signals: {signal: {formations, qualify_share}}, target, sessions, stocks}."""
    now = now or datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=int(sessions * 1.6) + 4)
    per_signal = {s: [] for s in STRENGTH_MEASURES}
    ranges = {1: [], 3: [], 5: []}
    with session_scope(session_factory) as session:
        symbols = [(s.symbol, s.exchange_segment) for s in get_active(session)]
        for symbol, segment in symbols:
            df = build_indicator_dataframe(session, symbol, segment, "1min", start, end, include_events=True)
            if df.empty:
                continue
            days = sorted(df[TRADING_DATE_COLUMN].unique())[-sessions:]
            df = df[df[TRADING_DATE_COLUMN].isin(days)].reset_index(drop=True)
            for minutes in ranges:
                ranges[minutes].append(_rolled_range_pct(df, minutes))
            for signal, measures in STRENGTH_MEASURES.items():
                fired = df[EVENTS_COLUMN].apply(lambda s, sig=signal: sig in s)
                if fired.any():
                    per_signal[signal].append(pd.DataFrame({m: parse_and_evaluate(f, df)[fired] for m, f in measures.items()}))
    med = {m: float(pd.concat(r).median()) for m, r in ranges.items() if r}
    multipliers = {"3min": round(med[3] / med[1], 2), "5min": round(med[5] / med[1], 2)} if med.get(1) else {}
    base, signals = {}, {}
    for signal, parts in per_signal.items():
        if not parts:
            continue
        m = pd.concat(parts).replace([float("inf")], 1e9).dropna(how="all")
        if m.empty:
            continue
        mins, share = _calibrate_signal(m, target)
        base[signal] = {k: round(float(v), 4) for k, v in mins.items()}
        signals[signal] = {"formations": int(len(m)), "qualify_share": round(share, 3)}
    return {"base": base, "multipliers": multipliers, "signals": signals,
            "target": target, "sessions": sessions, "stocks": len(symbols)}


def apply(session_factory, proposal: dict) -> None:
    """Writes a computed proposal as the SYSTEM minimums and multipliers
    (user overrides are untouched -- they still win for that user)."""
    for signal, measures in proposal["base"].items():
        for measure, value in measures.items():
            LibSignalStrength.set_rule(session_factory, signal, measure, value, owner_id=None)
    for tf, value in (proposal.get("multipliers") or {}).items():
        LibSystemSettings.set_value(session_factory, f"strength_multiplier_{tf}", value)
    LibSystemSettings.set_value(session_factory, "strength_target_share", proposal["target"])
    LibSystemSettings.set_value(session_factory, "strength_calibration_sessions", proposal["sessions"])
