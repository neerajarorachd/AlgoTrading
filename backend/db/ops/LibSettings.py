"""EngineSetting reads — calibrated parameter overrides for activity_engine.py
and prediction_tracker.py's own thresholds/formulas. Read-only from here;
a future calibration/backtesting pass writes these directly (a plain
upsert), not through this module."""
from __future__ import annotations

from typing import Dict, Sequence

from db.models import EngineSetting


def load_all(session) -> Dict[str, float]:
    return {row.key: float(row.value) for row in session.query(EngineSetting).all()}


def load_by_keys(session, keys: Sequence[str]) -> Dict[str, float]:
    rows = session.query(EngineSetting).filter(EngineSetting.key.in_(list(keys))).all()
    return {row.key: float(row.value) for row in rows}
