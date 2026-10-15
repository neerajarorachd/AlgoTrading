"""User preferences (2026-10-15) -- see db/models.py's UserPreference.
Values are stored as JSON text and handed back decoded."""
from __future__ import annotations

import json
from typing import Any, Dict

from db.models import UserPreference

MAX_KEY_LENGTH = 100
MAX_VALUE_LENGTH = 20000


class PreferenceError(ValueError):
    pass


def get_all(session, owner_id: int) -> Dict[str, Any]:
    out = {}
    for row in session.query(UserPreference).filter(UserPreference.owner_id == owner_id).all():
        try:
            out[row.key] = json.loads(row.value)
        except ValueError:
            continue  # a damaged row is skipped, never fatal
    return out


def set_many(session, owner_id: int, values: Dict[str, Any]) -> None:
    """Upsert each key; a value of None removes that key."""
    for key, value in values.items():
        if not isinstance(key, str) or not key or len(key) > MAX_KEY_LENGTH:
            raise PreferenceError(f"invalid preference key: {key!r}")
        row = session.query(UserPreference).filter_by(owner_id=owner_id, key=key).one_or_none()
        if value is None:
            if row is not None:
                session.delete(row)
            continue
        text = json.dumps(value, separators=(",", ":"))
        if len(text) > MAX_VALUE_LENGTH:
            raise PreferenceError(f"preference {key!r} is too large")
        if row is None:
            session.add(UserPreference(owner_id=owner_id, key=key, value=text))
        else:
            row.value = text
    session.flush()
