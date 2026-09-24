"""SystemSetting reads/writes — see db/models.py's SystemSetting for why
this is a separate table from EngineSetting. Unlike LibSettings.py (read-
only from code), this module owns both directions: recommendation_engine.py
reads through load_all/load_by_keys the same way activity_engine.py reads
EngineSetting; a future calibration UI (or a one-off script) writes through
set_value/set_many; seed_defaults is called once at app startup so every
key always has a real row to show/edit, never "missing means uncalibrated"
the way EngineSetting's emptiness is interpreted."""
from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

from db.models import SystemSetting
from db.session import session_scope


def load_all(session) -> Dict[str, float]:
    return {row.key: float(row.value) for row in session.query(SystemSetting).all()}


def load_by_keys(session, keys: Sequence[str]) -> Dict[str, float]:
    rows = session.query(SystemSetting).filter(SystemSetting.key.in_(list(keys))).all()
    return {row.key: float(row.value) for row in rows}


def set_value(session_factory, key: str, value: float, description: Optional[str] = None) -> None:
    """Upsert one setting. `description` is only applied when given — a
    plain value-only tune from a future UI shouldn't need to resend the
    description text every time."""
    with session_scope(session_factory) as session:
        row = session.query(SystemSetting).filter_by(key=key).one_or_none()
        if row is None:
            session.add(SystemSetting(key=key, value=value, description=description or ""))
        else:
            row.value = value
            if description is not None:
                row.description = description


def set_many(session_factory, values: Dict[str, float]) -> None:
    for key, value in values.items():
        set_value(session_factory, key, value)


def seed_defaults(session_factory, defaults: Sequence[Tuple[str, float, str]]) -> None:
    """Idempotent: inserts a row for any key not already present, leaves an
    already-existing row's VALUE untouched (a real tune must never be
    silently reset back to the module default on the next app restart) but
    refreshes its description. Call once at app startup, same spot
    seed_pattern_definitions/seed_strategy_elements are called from
    app.py's create_app()."""
    with session_scope(session_factory) as session:
        for key, value, description in defaults:
            row = session.query(SystemSetting).filter_by(key=key).one_or_none()
            if row is None:
                session.add(SystemSetting(key=key, value=value, description=description))
            else:
                row.description = description
