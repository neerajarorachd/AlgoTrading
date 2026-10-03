"""EngineSetting reads/writes — calibrated parameter overrides for
activity_engine.py and prediction_tracker.py's own thresholds/formulas.

Writes added 2026-10-03 (the "future calibration/backtesting pass" this
module's own docstring always pointed at) for the new Engine Settings admin
page — explicit instruction: once a backtest sweep (e.g.
Strategy.swing_lookback, see order_backtest.py's engine_config_from_strategy)
finds a winning value, PROMOTING it here is what makes live trading's one
shared ActivityEngine (app.py constructs exactly one, with no override) use
it too. Deliberately NO seed_defaults here, unlike LibSystemSettings.py's
sibling: EngineSetting's whole design is "a missing key means uncalibrated,
fall back to the module default" (see EngineSetting's own docstring) — this
module must never auto-create a row, or that distinction is lost."""
from __future__ import annotations

from typing import Dict, Optional, Sequence

from db.models import EngineSetting


def load_all(session) -> Dict[str, float]:
    return {row.key: float(row.value) for row in session.query(EngineSetting).all()}


def load_by_keys(session, keys: Sequence[str]) -> Dict[str, float]:
    rows = session.query(EngineSetting).filter(EngineSetting.key.in_(list(keys))).all()
    return {row.key: float(row.value) for row in rows}


def set_value(session, key: str, value: float, description: Optional[str] = None) -> None:
    """Upsert one calibrated override. `description` is only applied when
    given. Takes a plain session (not session_factory), same as load_all/
    load_by_keys above — the API route commits via Flask's own per-request
    session lifecycle (app.py's teardown_appcontext), not an explicit
    session_scope here."""
    row = session.query(EngineSetting).filter_by(key=key).one_or_none()
    if row is None:
        session.add(EngineSetting(key=key, value=value, description=description or ""))
    else:
        row.value = value
        if description is not None:
            row.description = description


def delete(session, key: str) -> bool:
    """Removes a calibrated override, reverting that key to "uncalibrated"
    (callers fall back to their own module default again). Returns whether a
    row actually existed to delete."""
    row = session.query(EngineSetting).filter_by(key=key).one_or_none()
    if row is None:
        return False
    session.delete(row)
    return True
