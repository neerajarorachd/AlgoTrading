"""Signal strength minimums (ALGO-6) -- see db/models.py's SignalStrengthRule
and signal_strength.py for the measures themselves."""
from __future__ import annotations

from typing import Dict, Optional

from db.models import SignalStrengthRule
from db.session import session_scope
from signal_strength import STRENGTH_MEASURES


class StrengthRuleError(ValueError):
    pass


def load_rules(session, owner_id: Optional[int] = None) -> Dict[str, Dict[str, float]]:
    """{signal: {measure: minimum}} -- system defaults, with this user's own
    rows laid over them. This is what EVENT(name) checks (context["strength_rules"])."""
    rules: Dict[str, Dict[str, float]] = {}
    rows = session.query(SignalStrengthRule).filter(SignalStrengthRule.owner_id.is_(None)).all()
    if owner_id is not None:
        rows += session.query(SignalStrengthRule).filter(SignalStrengthRule.owner_id == owner_id).all()
    for r in rows:  # user rows come last, so they win
        rules.setdefault(r.signal, {})[r.measure] = float(r.min_value)
    return rules


def set_rule(session_factory, signal: str, measure: str, min_value: float, owner_id: Optional[int] = None) -> None:
    if measure not in STRENGTH_MEASURES.get(signal, {}):
        known = ", ".join(sorted(STRENGTH_MEASURES.get(signal, {}))) or "none"
        raise StrengthRuleError(f"{signal} has no strength measure {measure!r} -- it has: {known}")
    with session_scope(session_factory) as session:
        row = session.query(SignalStrengthRule).filter_by(signal=signal, measure=measure, owner_id=owner_id).one_or_none()
        if row is None:
            session.add(SignalStrengthRule(signal=signal, measure=measure, min_value=min_value, owner_id=owner_id))
        else:
            row.min_value = min_value


def seed_defaults(session_factory, defaults: Dict[str, Dict[str, float]]) -> None:
    """Idempotent: inserts system minimums not already present; an existing
    one (tuned since) is never reset on restart."""
    with session_scope(session_factory) as session:
        existing = {(r.signal, r.measure) for r in session.query(SignalStrengthRule).filter(SignalStrengthRule.owner_id.is_(None))}
    for signal, measures in defaults.items():
        for measure, value in measures.items():
            if (signal, measure) not in existing:
                set_rule(session_factory, signal, measure, value, owner_id=None)


def load_multipliers(session) -> dict:
    """{'3min': x, '5min': y} from the system settings (1-min is the base)."""
    from db.ops import LibSystemSettings
    values = LibSystemSettings.load_by_keys(session, ["strength_multiplier_3min", "strength_multiplier_5min"])
    return {"3min": values.get("strength_multiplier_3min", 1.0), "5min": values.get("strength_multiplier_5min", 1.0)}


def rules_for_timeframe(session, timeframe: str, owner_id: Optional[int] = None) -> Dict[str, Dict[str, float]]:
    """The minimums EVENT(name) should check on this timeframe: the 1-min base
    (system + this user's overrides) with price-% measures scaled up."""
    from signal_strength import scale_minimums
    return scale_minimums(load_rules(session, owner_id), timeframe, load_multipliers(session))
