"""Pattern backtest results (2026-10-30) -- see db/models.py's
PatternBacktestResult. A run is saved as one "set" row (the whole stock set)
plus one "stock" row per symbol; the set row's numbers are also copied onto
the Pattern itself (win_rate / avg_move_pct / sample_size /
last_backtested_at), the read-only backtest stats the pattern list shows."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional, Sequence

from db.models import Pattern, PatternBacktestResult
from db.session import session_scope

_NUMERIC = (
    "capital", "leg_size", "slippage_pct", "win_rate", "avg_gross_pct", "avg_net_pct", "gross_rupees",
    "costs_rupees", "net_rupees", "net_after_slippage_rupees", "worst_drawdown_rupees",
    "y1_net_rupees", "y2_net_rupees",
)
_REQUIRED = ("run_label", "pattern_key", "combination", "timeframe", "scope", "exit_rule",
             "period_start", "period_end", "capital", "leg_size", "cost_model", "trades")


class BacktestResultError(ValueError):
    """A result row is missing a required field or has a bad scope."""


def _as_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def to_dict(r: PatternBacktestResult) -> dict:
    out = {c.name: getattr(r, c.name) for c in PatternBacktestResult.__table__.columns}
    for k in _NUMERIC:
        out[k] = float(out[k]) if out[k] is not None else None
    out["period_start"] = r.period_start.isoformat()
    out["period_end"] = r.period_end.isoformat()
    out["run_at"] = r.run_at.isoformat() + "Z"
    return out


def save_run(session_factory, rows: Sequence[dict], update_pattern_stats: bool = True) -> int:
    """Insert one run's rows. Each set row's numbers are copied onto its
    Pattern (matched by key, system/owner as given). Returns rows saved."""
    for row in rows:
        missing = [k for k in _REQUIRED if row.get(k) in (None, "")]
        if missing:
            raise BacktestResultError(f"result for {row.get('pattern_key')!r} is missing {missing}")
        if row["scope"] not in ("set", "stock"):
            raise BacktestResultError(f"scope must be 'set' or 'stock', not {row['scope']!r}")
        if row["scope"] == "stock" and not row.get("symbol"):
            raise BacktestResultError("a 'stock' row needs its symbol")
    columns = {c.name for c in PatternBacktestResult.__table__.columns} - {"id"}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with session_scope(session_factory) as session:
        patterns = {(p.key, p.owner_id): p for p in session.query(Pattern)}
        for row in rows:
            data = {k: v for k, v in row.items() if k in columns}
            data["period_start"] = _as_date(data["period_start"])
            data["period_end"] = _as_date(data["period_end"])
            data.setdefault("run_at", now)
            pattern = patterns.get((row["pattern_key"], row.get("owner_id")))
            if pattern is not None:
                data["pattern_id"] = pattern.id
            session.add(PatternBacktestResult(**data))
            if update_pattern_stats and pattern is not None and row["scope"] == "set":
                pattern.win_rate = row.get("win_rate")
                pattern.avg_move_pct = row.get("avg_net_pct")
                pattern.sample_size = row["trades"]
                pattern.last_backtested_at = now
    return len(rows)


def list_results(session, pattern_key: Optional[str] = None, scope: Optional[str] = None,
                 run_label: Optional[str] = None) -> List[dict]:
    """Newest first; filter by pattern key, scope and/or run."""
    q = session.query(PatternBacktestResult)
    if pattern_key:
        q = q.filter(PatternBacktestResult.pattern_key == pattern_key)
    if scope:
        q = q.filter(PatternBacktestResult.scope == scope)
    if run_label:
        q = q.filter(PatternBacktestResult.run_label == run_label)
    rows = q.order_by(PatternBacktestResult.run_at.desc(), PatternBacktestResult.id).all()
    return [to_dict(r) for r in rows]
